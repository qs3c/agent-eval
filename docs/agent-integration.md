# 标准 Agent 接入

平台面向本地单用户。Agent 的原生循环在任务容器里运行；平台提供任务、工作区、模型中继和截止时间，Harbor 继续负责容器、验证器与原始结果。不要把验证器或标准答案打包进 Agent。

开发者只提供 GitHub URL/ref 时，先固定 SHA、匹配已知配方；未知仓库可以通过 Integration Agent 生成候选，再走本页同一构建与检查契约。后续普通 push 与 RSI merge 均重新准入，运行中的实验不变。接入/repair 入口和前提见 [源码跟踪](source-tracking.md)。模型生成配方不能自行授予可运行或可比较等级。

## Recipe 与 Release

`integration validate --recipe recipe.yaml` 检查版本化配方。`integration build --recipe recipe.yaml --source /path/to/clean/checkout --root /path/to/platform` 将构建提交给 Manager，并等待结果。检查当前 checkout、记录确切 SHA、冻结配方及 patch，执行前再次核对 SHA；提交后源码变化会拒绝构建，不会悄悄改用新版本。

构建从 `git archive` 生成独立 eval 副本，在副本应用 patch。原 checkout 不修改。dirty、submodule 或 patch 冲突明确拒绝。源码链接默认拒绝；显式 `materialize_internal_links: true` 只允许物化归档内部目标，最终 bundle 仍禁止链接。当前不能声称支持任意仓库。源码来源必须与 checkout 的 origin 完全匹配，或显式填写该本地 checkout 的绝对路径。

构建容器使用 digest 固定镜像、非 root 用户、只读根目录、独立临时目录、CPU/内存/pid 限制，默认无网络；只挂载 eval 副本，不挂 Docker socket、宿主 home 或模型 key。依赖下载需要显式 `build.network: bridge`；依赖锁文件与镜像须由配方维护者固定。镜像需先由用户准备，构建不自动拉取。失败保留构建目录和日志；清理未确认时保留资源预留待核验。

Release 身份由 source SHA、recipe digest、patch digest、运行时镜像、profile 和产物 hash 组成。同样输入产生不同产物时获得不同 Release，不能覆盖旧身份。成功版本保存在 `.workspace/releases/<release-id>/`；构建证据在 `.workspace/builds/`。

## 最小完整配方

下面示例适用于 Linux，在 `/tmp/hello-harness-agent` 创建并提交一个独立 Git 仓库。镜像是已验证环境使用的固定 Python 镜像；在另一架构或离线环境中需准备兼容镜像并更新 digest。

`agent.py`：

```python
from pathlib import Path

def execute(request):
    Path(request["workspace"], "result.txt").write_text("ready\n")
    return {"schema_version": 1, "status": "completed"}
```

`build.py`：

```python
import shutil
from pathlib import Path

Path("dist").mkdir()
shutil.copy2("agent.py", "dist/agent.py")
```

将配方放在源码目录之外的 `recipe.yaml`：

```yaml
schema_version: 1
id: hello-harness
source:
  repository: /tmp/hello-harness-agent
  ref: main
build:
  image: python@sha256:4766d8b510c428e595d74b9cc5bbb2fae8e26316fffb4adc89908d79aacd58a2
  argv: [python3, build.py]
  output: dist
entrypoint:
  python: agent:execute
model_modes: [offline]
model_protocol: none
```

这是确定性协议夹具，不是一个真实模型 Agent。先在该目录执行 `git init -b main`、`git add agent.py build.py`、`git commit -m "Add protocol example"`（需要已配置 Git 作者身份），并准备配方指定的 Docker 镜像。打开“Agent 接入”，填写源码目录与配方绝对路径，点击“构建自己的版本”。构建完成后进入“创建对比”，只勾选 `hello-harness`，选择 `controls`、**离线 · 无模型协议检查**、两题，再预检和运行。此例不支持 mock/live，不能与需要模型的基线混在一次实验中。

真实 Agent 应按下节接入模型中继并声明实际支持的 `mock/live`，再与内置基线做同模式对比。CLI 也可使用 `integration list --root <平台目录>` 和 `integration agent --release <id> --root <平台目录>` 获取 Agent 配置对象，放入实验 JSON 的 `agents`，通过同一个 `run` 入口执行。

CLI Agent 使用 `entrypoint.argv` 替代 `entrypoint.python`，例如 `[python3, "{agent_dir}/agent.py", "{request_file}"]`。CLI 从 JSON 文件读取请求，并写入 `{output_dir}/agent-result.json`。Python 函数接收同一个请求 dict，返回同一个结果 dict，包装器负责写文件；Python 扩展仍只在容器内部导入。

请求字段：`schema_version=1`、`instruction`、`workspace`、`output_dir`、`deadline_unix`、`model`。模型包含 `mode/id/api_base/api_key_env/max_tokens`，其中 `api_key_env` 是环境变量名列表（可能为空），不是单个变量名。Python 可用 `next((os.environ[name] for name in model["api_key_env"] if name in os.environ), "relay-only")` 读取中继凭据。结果必须有 `schema_version=1` 和 `status=completed|failed`；Agent 自报 completed 仅指正常退出，benchmark 是否通过由独立验证器判定。标准输出、标准错误及请求/来源留在每次尝试的 `agent/` 目录。

## 模型与可观测性

真实 Agent 将 `model_modes` 配成实际支持的 `mock/live`，通过 `environment` 映射中继 URL、模型 ID，通过 `model_key_env` 声明需要的凭据环境变量名。容器中这些变量获得 `relay-only`；真实凭据由平台中继管理，配方不包含明文 key。支持的占位符为 `agent_dir/workspace/request_file/prompt_file/output_dir/model_id/api_base/home`。每次尝试使用独立 home。

`trace` 指向输出目录内原生事件文件，映射到 `events.jsonl`；`usage` 指向原始 usage 文件，保留为 `declared-usage.json`。声明的 usage 不自动成为可信费用；标准统计仍以匹配的模型中继请求/响应为依据，缺失费用保持未知。

`integration check --release <id> --run <实验目录> --root <平台目录>` 保存独立 conformance 记录，包含证据 hash：

- Runnable：至少两次独立尝试均有完整结果、来源和协议响应。
- Comparable：还需要完整模型角色、参数和网络覆盖证明；仅观察到部分中继调用不会授予该级别，未覆盖时保持不通过并说明原因。
- Diagnosable：在 Runnable 之外检查模型请求（原生 hook 或中继记录）、原生工具调用及工具结果；缺失时保持不通过。派生事件应指向原始输出，不冒充原生 provider hook。

该检查只对所验证的配方、Release、任务与环境负责，不表示任意 benchmark 通用。取消、超时、资源回收需通过专项集成验收。新 Release 不自动替换已经冻结的实验；源码跟踪与 RSI 准入指针由后续流程维护。
