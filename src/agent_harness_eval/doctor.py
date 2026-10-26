"""Read-only, dependency-light diagnostics; never install or call a model."""

import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
from pathlib import Path


def register(sub):
    parser = sub.add_parser("doctor", help="只读检查本地执行环境（不调用模型）")
    parser.add_argument("--agent", action="append", choices=["controls", "dsh", "bkcrab"])
    parser.add_argument("--benchmark", choices=["controls", "swe"], default="controls")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--min-free-gib", type=int, default=20)
    parser.add_argument("--port", type=int, help="可选：检查本机监听端口")
    parser.add_argument("--live", action="store_true", help="仅检查凭据是否存在，不发请求")
    parser.add_argument("--api-key-env", default="DEEPSEEK_API_KEY")
    parser.add_argument("--json", action="store_true", dest="as_json")


def probe(argv):
    try:
        result = subprocess.run(argv, capture_output=True, text=True, errors="replace", timeout=10)
        # Never print arbitrary tool stderr (may contain credentials/daemon URLs).
        return result.stdout.strip() if result.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def inspect(root, agents=(), benchmark="controls", min_free_gib=20, port=None,
            live=False, api_key_env="DEEPSEEK_API_KEY"):
    if min_free_gib < 0 or (port is not None and not 1 <= port <= 65535):
        raise ValueError("磁盘阈值必须非负，端口必须在 1–65535")
    agents = sorted(set(agents or ["controls"]))
    if not set(agents) <= {"controls", "dsh", "bkcrab"} or benchmark not in {"controls", "swe"}:
        raise ValueError("未知 Agent 或 benchmark")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", api_key_env):
        raise ValueError("无效的凭据环境变量名称")
    checks = []

    def add(identity, required_by, status, evidence, remediation="无需操作"):
        checks.append(dict(check_id=identity, required_by=required_by, status=status,
                           evidence=evidence, remediation=remediation))

    linux = platform.system() == "Linux"
    machine = platform.machine().lower()
    add("platform", ["execution"], "pass" if linux and machine in {"x86_64", "amd64"} else "fail",
        {"system": platform.system(), "machine": machine}, "执行基线为 Linux x86_64；Windows 原生不受支持")
    if "microsoft" in platform.release().lower():
        add("wsl", ["execution"], "warn", "检测到 WSL，尚未完成平台认证", "使用已验收的 Linux 主机，或单独完成 Docker 验收")
    python_status = "fail" if sys.version_info[:2] < (3, 12) else (
        "pass" if sys.version_info[:2] == (3, 12) else "warn"
    )
    add("python", ["platform"], python_status,
        platform.python_version(), "用 uv sync --locked --python 3.12 建立项目环境")
    add("process", ["execution"], "pass" if linux and Path("/proc/self/stat").is_file() and hasattr(os, "killpg") else "fail",
        {"proc": Path("/proc/self/stat").is_file(), "process_groups": hasattr(os, "killpg")},
        "需要 Linux /proc 与进程组支持；doctor 不发送进程信号")
    root = Path(root).resolve()
    try:
        if not root.is_dir():
            raise OSError("not a directory")
        free = shutil.disk_usage(root).free
        add("disk", ["execution"], "pass" if free >= min_free_gib * 1024**3 else "fail",
            {"free_gib": round(free / 1024**3, 2), "required_free_gib": min_free_gib},
            "释放目标工作区磁盘空间；该阈值是余量检查，不是 SWE 容量保证")
        add("workspace", ["platform"], "pass" if os.access(root, os.W_OK | os.X_OK) else "fail",
            {"accessible": os.access(root, os.W_OK | os.X_OK)}, "选择当前用户可写、可遍历的工作区")
    except OSError:
        add("disk", ["execution"], "fail", "工作区不存在或无法读取", "先创建工作区并确认权限")
    memory = None
    if linux:
        try:
            memory = int(re.search(r"MemAvailable:\s+(\d+)", Path("/proc/meminfo").read_text())[1]) // 1024
        except (OSError, TypeError, ValueError):
            pass
    add("capacity", ["execution"], "pass" if memory is not None else "warn",
        {"logical_cpus": os.cpu_count(), "host_available_memory_mb": memory},
        "根据实验并发配置 CPU/内存；此信息不代表容器配额或容量准入")

    dependencies = {"git": (["source"], None), "uv": (["platform"], (0, 13))}
    if "dsh" in agents:
        dependencies.update(node=(["dsh"], (22,)), npm=(["dsh"], None))
    if "bkcrab" in agents:
        dependencies["go"] = (["bkcrab"], (1, 25))
        source = os.environ.get("BKCRAB_SOURCE")
        ready = bool(source and (Path(source) / "cmd/bkcrab-eval").is_dir())
        add("bkcrab-source", ["bkcrab"], "pass" if ready else "fail",
            {"configured": bool(source), "eval_entry_present": ready},
            "设置 BKCRAB_SOURCE 指向含 cmd/bkcrab-eval 的 checkout；应用 patch 前先 git apply --check")
    for tool, (required_by, minimum) in dependencies.items():
        executable = shutil.which(tool)
        if not executable and tool == "uv" and os.access(root / ".tools/uv", os.X_OK):
            executable = str(root / ".tools/uv")
        output = probe([executable, "version" if tool == "go" else "--version"]) if executable else None
        match = re.search(r"\d+(?:\.\d+)+", output or "")
        version = tuple(map(int, match[0].split("."))) if match else None
        valid = version is not None and (minimum is None or version[:len(minimum)] >= minimum)
        add(tool, required_by, "pass" if valid else "fail",
            {"available": bool(executable), "version": match[0] if match else None},
            f"安装 {tool}" + (" >= " + ".".join(map(str, minimum)) if minimum else "") + " 并加入 PATH")
    docker = shutil.which("docker")
    info = probe([docker, "info", "--format", "{{json .}}"]) if docker else None
    try:
        data = json.loads(info) if info else {}
        if not isinstance(data, dict):
            data = {}
    except ValueError:
        data = {}
    add("docker", ["execution"], "pass" if data.get("OSType") == "linux" else "fail",
        {"cli_available": bool(docker), "linux_daemon": data.get("OSType") == "linux",
         "cpus": data.get("NCPU"), "memory_bytes": data.get("MemTotal")},
        "启动 Linux Docker daemon 并授予当前用户访问权限；不自动提权或启动服务")
    if benchmark == "swe":
        python313 = shutil.which("python3.13")
        output = probe([python313, "--version"]) if python313 else None
        add("swe-python", ["swe-prepare"], "pass" if output and output.startswith("Python 3.13.") else "warn",
            {"python313_on_path": bool(output and output.startswith("Python 3.13."))},
            "SWE 准备需要 Python 3.13；uv 可管理该解释器，本检查不下载；评分运行时由固定构建准备")
    if port is not None:
        try:
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", port))
            available = True
        except OSError:
            available = False
        add("port", ["web"], "pass" if available else "warn", {"port": port, "available": available},
            "选择空闲端口；此检查只测试 loopback，私网监听地址另行确认")
    add("model-credentials", ["live"] if live else ["mock"],
        "fail" if live and not os.environ.get(api_key_env) else "pass",
        {"mode": "live" if live else "mock", "present": bool(os.environ.get(api_key_env)) if live else None},
        "通过所选环境变量注入凭据；模型池凭据请在模型设置中检查，doctor 不读取或输出它")
    if live:
        add("model-connectivity", ["live"], "warn", "未测试：未发送任何 API 请求",
            "配置后另行授权模型协议 smoke；凭据存在不代表可用")
    return {"schema_version": 1, "agents": agents, "benchmark": benchmark,
            "ok": not any(item["status"] == "fail" for item in checks), "checks": checks}


def execute(args):
    try:
        result = inspect(args.root, args.agent, args.benchmark, args.min_free_gib,
                         args.port, args.live, args.api_key_env)
    except ValueError as exc:
        raise SystemExit(str(exc)) from None
    if args.as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        for check in result["checks"]:
            print(f"[{check['status']}] {check['check_id']}: {json.dumps(check['evidence'], ensure_ascii=False)}")
            if check["status"] != "pass":
                print(f"  {check['remediation']}")
    if not result["ok"]:
        raise SystemExit(1)
