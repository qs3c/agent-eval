# 标准 Agent 接入

平台面向本地单用户。Agent 的原生循环在任务容器里运行；平台提供任务、工作区、模型中继和截止时间，Harbor 继续负责容器、验证器与原始结果。不要把验证器或标准答案打包进 Agent。

本页是平台 Integration Agent、用户自带的 Codex/Claude Code 等开发工具以及人工开发者共同遵循的接入契约。平台检查交付物和实际运行证据，不限制配方的编写工具。开发工具负责接入，被测 Agent 负责执行评测任务，两者可以是完全不同的 Agent。

开发者只提供 GitHub URL/ref 时，先固定 SHA、匹配已知配方；未知仓库可以通过 Integration Agent 生成候选，也可以自行提供配方，再走本页同一构建与检查契约。后续普通 push 与 RSI merge 均重新准入，运行中的实验不变。接入/repair 入口和前提见 [源码跟踪](source-tracking.md)。模型生成配方不能自行授予可运行或可比较等级。

## 选择接入方式

**平台代办：** 在“Agent 接入”添加 GitHub 来源，等待观察到源码 SHA；已有匹配配方时进入构建与检查，缺少配方时选择“一键接入”并点击“开始一键接入”。当前实现使用 OpenCode、DSH、Hermes 的固定 Release 原生入口，需先按[平台 Agent 文档](platform-agents.md)准备通用运行时，在接入页面选择执行器和模型并确认费用。候选随后由平台构建与验收，生成成功不等于接入成功。

**自行接入：** 把平台 checkout、目标 Agent checkout 和本页交给自己的开发工具。它生成普通 recipe 与 patch，通过 CLI 或页面导入即可；不需要注册成平台执行器，也不需要调用 `source assist`。外部工具自身的模型调用由用户管理，平台的 `integration` 请求预算不会约束这些调用。两条路径交付物相同，可以在自动生成失败后转为外部工具修复。

## 自行接入操作步骤

以下构建与运行命令在已安装平台的 **Linux 主机**上执行，从平台仓库根目录运行；`/path/to/...`、`SOURCE_ID`、`RELEASE_ID` 均须替换为实际值。在其他机器编辑产物后，应先将配方及其相对路径引用的 patch 一起放到平台主机。Web 表单中的路径也是平台主机路径。

1. **阅读规范与双方源码。** 先读本页、[本地部署](local-deployment.md)和[模型策略](model-policies.md)，再检查目标 Agent 的依赖锁、CLI/SDK 入口、原生循环、模型路由、工具与日志。平台字段定义以 [`integrations/schema.py`](../src/agent_harness_eval/integrations/schema.py) 为准；运行请求和结果校验见 [`agents/recipe.py`](../src/agent_harness_eval/agents/recipe.py)，现有配方及 patch 可参考 [`integrations/recipes/`](../src/agent_harness_eval/integrations/recipes/)。不要直接复制其他 Agent 的源码 SHA、镜像 digest 或能力声明。
2. **固定目标源码与适配范围。** 记录 repository、ref 和完整 commit SHA，保持目标 checkout 干净。保留被测 Agent 的原生循环、工具和上下文管理；适配只负责构建、无界面入口、任务输入、模型参数和证据输出。关闭或改变的生产功能写入 `profile` / `limitations`。目标不支持无界面调用时，可用 patch 增加调用其原生循环的入口，不以新写的简化循环替代它。
3. **生成独立接入包。** 将 `recipe.yaml`（也接受 JSON）和必要的 patch 放在目标 checkout 之外。新增入口、构建脚本、依赖锁等若不在固定源码中，必须通过 `patches` 纳入 eval 副本；仅放在配方旁边的脚本不会自动进入构建容器。patch 路径相对配方目录，应用路径相对目标仓库根目录。依赖下载需显式声明 `build.network: bridge`，镜像 digest 应来自实际准备的镜像。
4. **先做配方检查，再构建固定 Release。** `validate` 只检查字段契约，不执行构建，不证明 patch 可应用、模型可达或 Agent 可运行。

   ```bash
   uv run agent-harness-eval integration validate --recipe /path/to/integration/recipe.yaml
   uv run agent-harness-eval integration build --recipe /path/to/integration/recipe.yaml --source /path/to/clean/agent --root /path/to/platform
   uv run agent-harness-eval integration list --root /path/to/platform
   uv run agent-harness-eval integration agent --release RELEASE_ID --root /path/to/platform
   ```

5. **运行隔离控制并核验。** 在“创建对比”选择该 Release、`controls` 两题、重复一次：模型 Agent 选 mock；无模型协议夹具选 offline。预检后运行，再回到“Agent 接入”选择该 Release 与已结束实验，点击“核验运行证据”。CLI 可将上一步返回的 Agent 配置放入独立实验 JSON 的 `agents`，通过 `run` 执行，然后核验：

   ```bash
   uv run agent-harness-eval integration check --release RELEASE_ID --run /path/to/platform/artifacts/your-control-run --root /path/to/platform
   ```

   标准来源自动准入要求支持 mock 或 offline；仅支持 live 的 Agent 不能通过这条自动准入流程。模拟模型必须仍驱动目标 Agent 的真实循环；不得识别控制题后硬编码答案。控制实验不需要真实模型密钥；真实模型能力验证是单独的后续步骤。
6. **需要持续跟踪时提交来源配方。** 新来源可用下列命令；已有来源则用 `source repair` 绑定外部工具生成的配方。页面对应“接入配方绝对路径”或“修复配方路径”。这些操作会排队构建与运行检查，通过 `source revisions` 或页面查看结果。

   ```bash
   uv run agent-harness-eval source add --repository https://github.com/OWNER/AGENT --ref main --recipe /path/to/integration/recipe.yaml --root /path/to/platform
   # 已有来源时，使用下面的 repair 命令替代 add。
   uv run agent-harness-eval source repair --source-id SOURCE_ID --recipe /path/to/integration/recipe.yaml --root /path/to/platform
   uv run agent-harness-eval source revisions --source-id SOURCE_ID --root /path/to/platform
   ```

   来源跟踪会把配方重新固定到**当前观察到的 SHA**，不是永久使用配方里原来的 commit。若只验收指定 SHA，使用第 4–5 步的直接构建路径；源码更新后须重新检查 patch 和运行证据，不能沿用旧 SHA 的验收结论。正在准入的来源须先等待完成或取消作业再提交修复。

## 接入交付物与共同要求

| 交付物 | 要求 |
| --- | --- |
| `recipe.yaml` / `recipe.json` | `schema_version: 1`；定义来源、固定镜像、构建 argv、输出目录、CLI/Python 入口、模型模式/协议与能力；不得加入 schema 未定义字段 |
| 接入 patch（如需） | 在 `patches` 中依序列出；可应用到指定 SHA 的干净副本；包含构建或运行所需但上游尚无的文件 |
| 构建产物 | `build.output` 内包含入口及其所需运行时/依赖，或明确依赖并验证目标任务镜像已有的运行时；不能依赖开发者的宿主 home 或全局安装 |
| 中文交接说明 | 目标 SHA、原生入口、适配修改、功能差异、模型与网络覆盖、产物路径、已执行命令及证据、失败原因与未验证事项；可放在接入包的说明文件中 |
| 平台验收记录 | 构建成功后的 Release ID、至少两次完整隔离尝试和 `integration check` 结果；环境未具备时只交付候选并注明未构建/未验收 |

构建镜像仅用于构建；实际 Agent 在 benchmark 的任务容器中运行，并不会自动继承构建镜像的 Python/Node/系统库。新增 Agent 通常只需标准配方和薄入口，复用平台通用适配器，不需要修改平台核心或 benchmark。

源码跟踪与平台自动生成路径要求 `build.artifact_origin: source`（默认值）。独立导入虽支持显式 `registry-package`，但安装发布包不能作为“已构建这个源码 SHA”的证据。不得将验证器、参考答案或隐藏测试打包到 Agent，或以修改评分逻辑取得接入通过。

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

请求字段：`schema_version=1`、`instruction`、`workspace`、`output_dir`、`deadline_unix`、`model`。模型包含 `mode/id/api_base/api_key_env/max_tokens/context_window`，其中 `api_key_env` 是环境变量名列表（可能为空），不是单个变量名。Python 可用 `next((os.environ[name] for name in model["api_key_env"] if name in os.environ), "relay-only")` 读取中继凭据。结果必须有 `schema_version=1` 和 `status=completed|failed`；Agent 自报 completed 仅指正常退出，benchmark 是否通过由独立验证器判定。标准输出、标准错误及请求/来源留在每次尝试的 `agent/` 目录。

入口应使用 `instruction` 驱动原生 Agent，在 `workspace` 读写任务文件，把协议响应和日志写到 `output_dir`，并尊重 `deadline_unix`。CLI 正常完成需退出码为 0 且存在有效 `agent-result.json`；失败应如实返回失败，非零退出或缺少有效结果都会导致运行检查失败。入口不能依赖交互式登录或跨任务保留的用户状态。

## 模型与可观测性

真实 Agent 将 `model_modes` 配成实际支持的 `mock/live`，通过 `environment` 映射中继 URL、模型 ID，通过 `model_key_env` 声明需要的凭据环境变量名。容器中这些变量获得 `relay-only`；真实凭据由平台中继管理，配方不包含明文 key。支持的占位符为 `agent_dir/workspace/request_file/prompt_file/output_dir/model_id/api_base/home`。每次尝试使用独立 home。

`trace` 指向输出目录内原生事件文件，映射到 `events.jsonl`；`usage` 指向原始 usage 文件，保留为 `declared-usage.json`。声明的 usage 不自动成为可信费用；标准统计仍以匹配的模型中继请求/响应为依据，缺失费用保持未知。

`integration check --release <id> --run <实验目录> --root <平台目录>` 保存独立 conformance 记录，包含证据 hash：

- Runnable：至少两次独立尝试均有完整结果、来源和协议响应。
- Comparable：要求完整模型角色、参数和网络覆盖证明；当前检查器始终返回不通过并记录覆盖缺口，不能靠配方声明或一次成功运行授予该级别。
- Diagnosable：在 Runnable 之外检查模型请求（原生 hook 或中继记录）、原生工具调用及工具结果；缺失时保持不通过。派生事件应指向原始输出，不冒充原生 provider hook。

该检查只对所验证的配方、Release、任务与环境负责，不表示任意 benchmark 通用。取消、超时、资源回收需通过专项集成验收。新 Release 不自动替换已经冻结的实验；源码跟踪与 RSI 准入指针由后续流程维护。
