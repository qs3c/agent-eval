"""Run the existing native Agent loops in isolated Docker containers."""

import argparse
import json
import os
import signal
import subprocess
import sys
import tempfile
import shutil
from pathlib import Path
from uuid import uuid4

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agent_harness_eval.model_pool import ModelPool
from agent_harness_eval.rsi.executors import credential
from agent_harness_eval.rsi.language import DIAGNOSIS_LANGUAGE_INSTRUCTIONS


def run_container(image, mounts, command, timeout, *, env=None):
    name = "eval-rsi-" + uuid4().hex
    argv = [
        "docker",
        "run",
        "--rm",
        "--init",
        "--name",
        name,
        "--user",
        f"{os.getuid()}:{os.getgid()}",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--pids-limit",
        "256",
        "--memory",
        "4g",
        "--cpus",
        "2",
        "--workdir",
        "/workspace",
    ]
    for source, target, readonly in mounts:
        argv += [
            "--mount",
            f"type=bind,src={source},dst={target}" + (",readonly" if readonly else ""),
        ]
    for key, value in (env or {}).items():
        argv += ["--env", f"{key}={value}"]
    argv += [image, "timeout", "-k", "10", str(timeout), *command]

    def interrupted(signum, frame):
        raise InterruptedError("Native executor interrupted")

    previous = signal.signal(signal.SIGTERM, interrupted)
    try:
        result = subprocess.run(argv, timeout=timeout + 30, check=False)
        if result.returncode:
            raise RuntimeError(
                f"执行 Agent 容器退出，状态码 {result.returncode}；请查看候选执行日志"
            )
    finally:
        signal.signal(signal.SIGTERM, previous)
        subprocess.run(
            ["docker", "rm", "-f", name],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=20,
            check=False,
        )


def execute(args, context):
    workspace = Path(context["workspace"]).resolve()
    evidence = Path(context["evidence_directory"]).resolve()
    result = Path(context["result_path"]).resolve()
    output = result.parent / "output/native"
    output.mkdir(parents=True, exist_ok=True)
    logs = output / "logs"
    logs.mkdir(exist_ok=True)
    instruction = {
        **context,
        "workspace": "/workspace",
        "evidence_directory": "/evidence",
        "result_path": "/output/diagnosis.json",
    }
    prompt = output / "instruction.txt"
    prompt.write_text(
        "You are the execution Agent for an evidence-driven code improvement.\n"
        "Read /evidence/index.json first. Modify /workspace and add regression coverage. "
        "Do not commit or push. Do not modify evidence. Write valid diagnosis JSON matching result_schema "
        "to /output/diagnosis.json before finishing. Cite only exported evidence IDs.\n"
        + DIAGNOSIS_LANGUAGE_INSTRUCTIONS
        + "\n"
        + json.dumps(instruction, ensure_ascii=False),
        encoding="utf-8",
    )
    regression = context.get("purpose") == "targeted_regression_plan"
    integration = context.get("purpose") == "integration_recipe"
    role = "integration" if integration else "diagnosis" if regression else "improvement"
    if integration:
        prompt.write_text(context["instruction"], encoding="utf-8")
    if regression:
        from agent_harness_eval.rsi.regression import planning_prompt

        prompt.write_text(planning_prompt(context), encoding="utf-8")
    selected_policy = getattr(args, "diagnosis_model_policy_json", None) if regression else None
    selected_policy = selected_policy or getattr(args, "model_policy_json", None)
    profile = json.loads(selected_policy) if selected_policy else {
        "mode": "live", "name": args.model, "api_base": getattr(args, "api_base", "https://api.deepseek.com"), "messages_base": getattr(args, "messages_base", None),
        "max_tokens": getattr(args, "max_tokens", 32768), "context_window": getattr(args, "context_window", 1000000),
        "api_key_env": "EVAL_MODEL_API_KEY", "policy": {"protocols": ["anthropic-messages"] if args.executor == "dsh" else ["openai-chat"]}}
    selected_key = profile.get("api_key_env", "")
    if selected_key.startswith("EVAL_POOL_KEY_"):
        args.key_ref = selected_key.removeprefix("EVAL_POOL_KEY_")
    key = (
        ModelPool(args.root).credential(args.key_ref)
        if getattr(args, "key_ref", None)
        else credential(args.root)
    )
    if not key:
        raise ValueError("模型 API Key 未配置")
    with tempfile.TemporaryDirectory(prefix="eval-rsi-key-") as temporary:
        secret = Path(temporary) / "key"
        secret.touch(mode=0o600)
        secret.write_text(key)
        from agent_harness_eval.policies.model import freeze
        from agent_harness_eval.provenance import write_json

        policy = freeze(profile, role)
        relay = output / "relay"
        relay.mkdir(exist_ok=True)
        resources = Path(__file__).parents[1] / "resources"
        for name in ("policy_model.py", "policy_runtime.py", "search_review.py"):
            shutil.copy2(resources / name, relay / name)
        shutil.copy2(resources / ("mock_model.py" if policy.model["mode"] == "mock" else "live_model.py"), relay / "policy_upstream.py")
        write_json(relay / "model-policy.json", {"policy": policy.model_dump(mode="json"), "digest": policy.digest})
        write_json(logs / "model-policy.json", {"policy": policy.model_dump(mode="json"), "digest": policy.digest})
        mounts = [
            (workspace, "/workspace", regression),
            (evidence, "/evidence", True),
            (output, "/output", False),
            (logs, "/logs/agent", False),
            (prompt, "/installed-agent/prompt.txt", True),
            (secret, "/run/secrets/model-key", True),
            (relay, "/platform-relay", True),
        ]
        if integration:
            mounts.append((Path(context["source_directory"]).resolve(), "/source", True))
        if args.executor == "bkcrab":
            command = [
                "sh",
                "-c",
                'export EVAL_MODEL_API_KEY=relay-only; exec "$@"',
                "rsi",
                "/installed-agent/bkcrab-eval",
                "--workspace",
                "/workspace",
                "--home",
                "/tmp/bkcrab-rsi-home",
                "--output",
                "/logs/agent",
                "--prompt-file",
                "/installed-agent/prompt.txt",
                "--model",
                profile["name"],
                "--api-base",
                "http://127.0.0.1:18765/v1",
                "--max-tokens",
                str(getattr(args, "max_tokens", 32768)),
                "--context-window",
                str(getattr(args, "context_window", 1000000)),
                "--timeout",
                str(args.timeout) + "s",
            ]
        else:
            command = [
                "sh",
                "-c",
                'export DEEPSEEK_API_KEY=relay-only; exec "$@"',
                "rsi",
                "/installed-agent/dsh/node",
                "/installed-agent/dsh/node_modules/@deepseek-ai/dsh/lib/bin.js",
                "--profile",
                "sdk",
                "--patch",
                "/installed-agent/dsh/transport.patch.yml",
                "--patch",
                "/installed-agent/dsh/standard.patch.yml",
            ]
        command = ["sh", "-ec", """mkdir -p /tmp/rsi-policy
cp /platform-relay/model-policy.json /tmp/rsi-policy/model-policy.json
cp /run/secrets/model-key /tmp/rsi-policy/live.key
chmod 600 /tmp/rsi-policy/live.key
HARNESS_POLICY_DIRECTORY=/tmp/rsi-policy python3 /platform-relay/policy_model.py >/logs/agent/model-server.log 2>&1 &
relay_pid=$!
trap 'kill "$relay_pid" 2>/dev/null || true' EXIT
python3 -c 'import socket,time
for _ in range(100):
 try:
  socket.create_connection(("127.0.0.1",18765),.1).close(); break
 except OSError: time.sleep(.05)
else: raise RuntimeError("platform model relay did not start")'
"$@"
""", "platform-role", *command]
        run_container(
            args.image,
            mounts,
            command,
            args.timeout,
            env={
                "HOME": "/tmp/rsi-home",
                "DSH_HOME": "/tmp/dsh-rsi-home",
                "DSH_PERMISSION_MODE": "danger-full-access",
                "DEEPSEEK_BASE_URL": "http://127.0.0.1:18765",
                "EVAL_MODEL": profile["name"],
                "EVAL_MAX_TOKENS": str(getattr(args, "max_tokens", 32768)),
                "DSH_PRIMARY_RUNTIME": "",
                "DSH_TOOLS_MODE": "native",
                "CGO_ENABLED": "0",
                "GOCACHE": "/tmp/go-cache",
                "GOPATH": "/tmp/go",
            },
        )
    from agent_harness_eval.policies.model import verify_usage

    requests = logs / "policy-requests.jsonl"
    write_json(logs / "model-policy-report.json", verify_usage(policy, {
        "requests": [json.loads(line) for line in requests.read_text().splitlines() if line.strip()] if requests.exists() else [],
        "cost_usd": None}))
    diagnosis = output / ("integration.json" if integration else "plan.json" if regression else "diagnosis.json")
    if (
        diagnosis.is_symlink()
        or not diagnosis.is_file()
        or diagnosis.stat().st_size > 256 * 1024
    ):
        raise ValueError("执行 Agent 未生成有效的诊断结果文件")
    # The runner validates the schema and evidence references before accepting it.
    value = json.loads(diagnosis.read_text())
    result.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", required=True)
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--root", type=Path)
    parser.add_argument("--executor", choices=["bkcrab", "dsh"])
    parser.add_argument("--model")
    parser.add_argument("--api-base", default="https://api.deepseek.com")
    parser.add_argument("--messages-base")
    parser.add_argument("--key-ref")
    parser.add_argument("--max-tokens", type=int, default=32768)
    parser.add_argument("--context-window", type=int, default=1000000)
    parser.add_argument("--model-policy-json")
    parser.add_argument("--diagnosis-model-policy-json")
    parser.add_argument("--check-target", choices=["bkcrab", "dsh", "dsh-standard"])
    parser.add_argument("--workspace", type=Path)
    args = parser.parse_args()
    if args.check_target:
        command = (
            ["sh", "/installed-agent/check-bkcrab.sh"]
            if args.check_target == "bkcrab"
            else [
                "git",
                "-c",
                "safe.directory=/workspace",
                "show",
                "--check",
                "--format=",
                "HEAD",
            ]
        )
        run_container(
            args.image,
            [(args.workspace.resolve(), "/workspace", False)],
            command,
            args.timeout,
            env={
                "HOME": "/tmp/rsi-home",
                "CGO_ENABLED": "0",
                "GOCACHE": "/tmp/go-cache",
                "GOPATH": "/tmp/go",
            },
        )
    else:
        execute(args, json.load(sys.stdin))


if __name__ == "__main__":
    main()
