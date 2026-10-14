# 实验运行守护

固定监测程序负责发现疑似停滞、先留存现场、启动守护 Agent。守护 Agent 自主分析并执行目标容器中的诊断/恢复 shell；没有故障类型到修复动作的规则表。平台的独立守护监督进程（`supervisor.py`，不是 Harbor）包含监测逻辑、管理 Harbor 题目工作进程和守护驱动，负责原始硬期限、结构化决策和资源清理。明确无法恢复或超过硬期限的题目保留失败，释放并发槽位，继续后续题目。

这是 RSI 的运行期证据入口。本版不包含修改 harness 源码、创建 PR、候选版本复评或 DGM 多代搜索；现在可从全局模型池指定守护模型。协议测试使用确定性脚本，不代表模型诊断能力。

## 启用

运行目标为 Linux + Docker，默认关闭。不带 `guardian` 的实验继续使用原 Harbor Job。开启后使用独立 Harbor Trial 工作进程；每次尝试仍由 Harbor 执行与评分。

首版覆盖当前平台的单阶段、共用任务容器评分模式。Harbor 多阶段任务和独立评分器环境会在创建运行产物前明确拒绝，避免把尚未覆盖的容器生命周期当作已支持。

仓库提供不调用模型的观察模式模板：

```sh
uv run agent-harness-eval run experiments/guardian-controls.json --output artifacts/guardian-controls-001
```

模板会出现在控制台模板列表。修改模板只影响之后创建的实验，不修改历史运行。`observe` 模式保存事件，并在阶段硬期限后兜底；日志静默本身只记录，不立即杀题。

接入自己的可信 Agent 适配器时，在实验 JSON 中配置：

```json
{
  "guardian": {
    "mode": "assist",
    "command": ["/absolute/path/to/python", "/absolute/path/to/guardian_adapter.py"],
    "version": "adapter-git-sha-and-model-version",
    "pass_env": ["GUARDIAN_API_KEY"],
    "poll_seconds": 15,
    "idle_seconds": 600,
    "confirmations": 2,
    "tool_grace_seconds": 45,
    "diagnostic_seconds": 120,
    "command_seconds": 20,
    "cleanup_seconds": 30,
    "max_incidents": 3,
    "max_actions": 12
  }
}
```

`command` 是 argv 数组，不经过宿主机 shell 展开。工作目录是本次 incident；脚本路径使用绝对路径，或使用已安装模块 `python -m package`。`version` 必填；应固定适配器、模型和提示词版本。凭据通过 `pass_env` 白名单传递，不放在参数或配置中。适配器是管理员安装的可信宿主机程序，不是安全沙箱；容器工具接口只定位当前题。平台会记录经协议调用的操作，适配器应通过这些接口进行介入，不能自行绕过日志通道操作宿主机。

守护模型费用与被测 Agent 费用是不同项目；当前结果中的 token/USD 仍只代表原评测统计。适配器可将自己的用量写入 `finish.arguments.usage`，未提供时未知，平台不将其猜测为零。在全局模型设置分配守护模型后，新建 assist 实验会冻结该模型并使用内置 Chat Completions JSONL 驱动；未指定模型时继续使用模板自定义驱动。observe 模式不调用模型，分配模型不会自动开启介入。

## 发现与期限

- 定时读取 Harbor 生命周期：创建、环境启动、Agent 开始/结束、验证和清理。
- 默认每 15 秒轮询、连续 600 秒无日志进展且两次命中才触发调查。轮询频率和静默阈值是不同参数；历史冻结配置不自动修改。
- 日志尾部仅用于展示和诊断。Docker 监测单独按文件身份、字节偏移和边界校验增量消费完整事件行；普通诊断 snapshot 不推进监测游标。每轮有界读取，积压在后续轮询追赶；半行等待补齐。轮转、截断、损坏或超过单行上限会记录 gap 并清除不可信 pending，证据不完整时不能判工具逾期。
- BKCrab 的 exec 使用显式 timeout，否则使用 120 秒默认值。DSH 的 `tool/call`/`tool/result` 同样配对，`job_output(wait=true)` 按 timeout_ms（默认 30 秒，上限 600 秒）识别等待；明确有效工具等待期限与宽限期内不产生静默报警。自定义 DSH preset 改变此上限时需同步适配，当前针对冻结 Standard 约定。stderr 输出和单次 CPU 值不直接算有效进展。
- 进程快照只读 PID/PPID、状态、启动计数、累计 CPU 时间、进程名和 FD 链接；不读内存或环境变量。守护可对比多次快照以及原命令输出文件变化判断是否仍在工作。
- Agent 期限使用 Harbor 实际解析后的 timeout。守护操作在原期限到达或阶段结束后失效；允许 Harbor 在 `cleanup_seconds` 内结束取消和清理，随后独立强制兜底。守护不会增加题目的作答预算。
- `setup_seconds` 默认 1200，为创建/环境等阶段的外层兜底；`verifier_seconds` 默认 1200，为验证阶段外层上限。它们不改写 task.toml 的评分器超时。诊断和单次命令各有独立期限。旧配置的 verification_seconds 仅兼容读取，不再启动恢复验证倒计时。
- 守护以 `final`（兼容 `finish`）提交两个决策之一：`continue` 保存介入记录并继续运行原题，`terminate` 附具体原因与证据后由监督进程终止、清理本题并让队列继续。正常等待、误报、解除阻塞和不确定性都写入 summary，不再划分决策分支。旧 wait/false_positive/recovered/inconclusive 仅兼容转换为 continue。
- “恢复”只指解除实验当前运行阻塞，让原 Agent 循环继续；不允许修复、热补丁、替换或升级被测 harness 的源码、依赖、提示词、配置和工具实现，也不能伪造工具返回、重启整个 Agent 掩盖故障。可以在确认影响后终止失控的工具子进程，让原 Agent 收到真实错误并继续处理。需要修改 harness 或会话已不可恢复时，记录证据后终止本题，根因修复留到独立工作。
- 守护操作后在 final 之前、调查预算内可再次采集现场，检查原工具返回或原 Agent 后续进展；命令执行成功不等于恢复。final 提交后本次守护立即结束，后续进展只由监督进程常规监测，必要时另启调查。continue 只表示继续运行，outcome 记录 continued，不声称修复成功。继续运行或驱动失败后，保持常规监测，经过 idle_seconds 再允许调查；驱动失败记为 investigation_failed，不当作被测 Agent 卡死。删除单独的恢复验证倒计时和相关状态，原题硬期限与调查次数上限继续生效。
- 兜底先终止该题工作进程，再清理精确 Compose project 下的容器和网络。失败保留 `resources_released: false`，不会谎报清理成功。SIGTERM/SIGINT 正常取消走清理流程；宿主机关机、SIGKILL 或 Docker daemon 故障不受本进程保证，仍需已有的控制台 interrupted/recover 流程。

## JSONL 插件协议 v1

### 版本核对与待完善的诊断续期

2026-10-14 更正：先前根据本地旧基线 f78d324 判断“没有异常保留实现”不适用于昨天的实际运行版本。服务器包含 2026-10-13 提交 b51b273（bounded batches with forensic retention），其 guardian/retention.py 与 batching.py 已实现并启用保留策略；supervisor/worker 通过 keep_containers 暂留已停止容器，归档后再精确删除。本地此次守护补丁尚未整合这批后续提交，不能直接覆盖服务器文件；先前 217 项全量及 42 项针对性验证仅证明旧基线补丁通过，不能作为与批次/保留模块整合后的验收。

实际 SWE80 冻结配置为每批 20 题，每实验最多 3 份归档、总计 20 GiB、24 小时；期限在下一次归档时检查，不是定时立即删除。只保留过滤后的 /testbed 为 retained-workspace.tar，排除凭据相关文件与符号链接，不保留完整容器、进程内存、/tmp 或整个根文件系统。异常、外部辅助或关键证据缺失会触发；正常 reward=0 且证据完整不会触发。2026-10-14 只读核查 160 条 retention.json：123 条正常不归档、34 条已淘汰、3 条归档仍在，没有 retention_failed。现存三份为 DSH 的 django-14631、pylint-4661、pytest-7521，总计 187453440 字节。四批均有 cleanup.json，记录了当前批镜像删除、受保护标签保留及空间变化。核查源码副本位于 .cache/eval-80-review/deployed-retention/。

当前守护诊断默认 120 秒且受到原题 active 检查约束，题目到期或阶段变化会取消守护。建议增加独立且有界的诊断租约：在做题期限前启动的调查获得完整诊断窗口，期间延后现场删除；做题到期后允许继续只读诊断及提交 final，但不得自动重新启动或延长被测 Agent 作答。若需要恢复后补足作答时间，则属于另一项需明确计时契约的续期，必须协调 Harbor、监督进程和 harness 自身超时，不能只改外层期限。此段是待实施设计，不代表续期已生效。

监督进程首先向驱动 stdin 写入一行 JSON：`protocol: eval-guardian/v1`、incident 绝对路径、工具名、动作上限和任务约束。JSONL 是驱动与监督进程之间的协议，不是模型输出格式。内置模型驱动使用 API 原生函数工具，提供有效的结构化证据摘要；宿主机证据通过白名单 `evidence(name, offset)` 分块读取，exec 只在题目容器里运行。

```json
{"id":"inspect-1","tool":"snapshot","arguments":{}}
{"id":"act-1","tool":"exec","arguments":{"command":"ps -eo pid,ppid,stat,comm"}}
{"id":"done","tool":"final","arguments":{"decision":"continue","summary":"全量测试仍在运行，输出写入原命令的日志文件","evidence":["两次快照中测试进程累计 CPU 增长"]}}
```

每个 id 唯一。snapshot/exec/evidence 请求会在 stdin 得到 `{"id":"...","result":{...}}`。exec 为目标容器 root shell，有独立进程组、容器内超时以及各 64 KiB 的 stdout/stderr 上限；超量输出继续被读取丢弃，避免管道阻塞或无界写盘。final/finish 保存总结并结束会话，不需要等待响应。无效 JSON、超长协议行、失联、超时或耗尽动作预算会留下结果并终止驱动进程组；调查失败不等同于被测 Agent 卡死。

内置驱动默认请求串行工具；若收到多个原生调用，先校验整批再逐个执行。零调用/无效参数最多连续纠正两次，保留最后一次动作预算用于 final。`model-responses.jsonl` 记录调用数量、finish_reason、用量、有界脱敏响应与协议错误；不将失败模型响应伪装成工具已经执行。新版内置驱动版本为 `model-pool-driver/v2`。系统提示见 `guardian/decisions.py` 的 `SYSTEM_PROMPT`。

## DSH 模型与搜索中转

测评容器显式设置主模型 `DEEPSEEK_BASE_URL=http://127.0.0.1:18765` 和独立搜索 `DEEPSEEK_SEARCH_BASE_URL=http://127.0.0.1:18765/search/v1`。`relay-only` 只作为本地占位凭据，中转使用真实凭据向实验配置的上游发请求，不记录认证头。

搜索 `/search/v1/messages` 保留原始 model、max_tokens、server tools 等搜索契约，不套用主 Agent 的采样归一化；用 purpose=web_search 记录请求、用量和时序。普通模型调用（包括使用同一 provider 的子 Agent）沿用主中转。冻结 DSH 包中另一个独立 DeepSeek 请求入口是搜索；普通 `web_fetch` 读取网页，未使用 DeepSeek 凭据，不转发到模型 API。离线 mock 明确返回不提供搜索，不伪造在线搜索成功。

操作前检查原容器 ID、Compose 标签、存活状态、题目阶段及时间权限。该检查降低现场变化风险，但不是与 Harbor 生命周期的跨进程事务；已开始的容器命令仍受自己的短超时约束。

## 证据及控制台

每题目录保存 `guardian-state.json`、`guardian-status.json`、工作进程日志和 Agent 结束、容器删除前的快照。每个 `incidents/<id>/` 包含：

| 文件 | 产生者与内容 |
|---|---|
| incident.json | 固定监测程序：触发时间、原因、题目/Agent、当次策略 |
| host.json | 固定采集器：宿主机状态与日志尾部，来源路径、偏移、大小、哈希或读取错误 |
| initial.json | 固定采集器：介入前容器 ID/状态、有限 `/proc` 进程身份和 FD 链接、日志尾部；失败也留记录 |
| driver-context.json / driver-stderr.json | 平台保存的插件输入与有限 stderr |
| actions.jsonl | 平台逐次记录请求、实际返回，以及独立兜底动作/结果 |
| assisted.json | 执行动作前写入的外部辅助标记，未知结果也保守标记 |
| diagnosis.json / driver-result.json | Agent 总结与驱动运行结局，和平台判断分开 |
| model-responses.jsonl | 内置模型驱动响应与协议诊断，仅保留有界脱敏正文；公共视图不展示推理 |
| after.json / outcome.json | 后续现场和平台观测到的进展/结束/终止结局 |

`/proc` 采集只读固定进程元信息及 FD 链接，不递归扫描进程文件，不读 pagemap、内存或环境变量。原始日志可能含私有任务内容，保留在原实验权限范围中。快照以首次写入为准，后续采集另存；不是防管理员篡改的审计存储。历史人工调查记录不会被伪装成自动采集；已有 `human-intervention.json` 的辅助标记可读取，但无法追补已销毁现场。

运行详情新增“实验运行守护”，支持事件列表、诊断摘要与命令记录。只读接口：`GET /api/runs/{id}/incidents` 和 `GET /api/runs/{id}/incidents/{incident_id}`；公开视图不返回日志正文、原始快照或模型推理。原始奖励不改写，报表/逐题比较显示外部辅助；跨批次检查守护策略与介入标记，不能把受辅助结果解释为纯自主成绩。

## 验证

2026-10-14 后续简化验收：在同一服务器隔离目录运行 `tests/test_guardian.py`、`tests/test_guardian_decisions.py`、`tests/test_guardian_telemetry.py`、`tests/test_guardian_docker.py`，启用 Docker，**42 passed，102.25 秒**。验证两种决策、旧协议兼容、继续运行后原期限仍生效、调查失败隔离和资源清理。本地相关测试 14 passed、5 skipped；Ruff 与 diff 检查通过。仍未部署或调用真实模型 API。以下 217 项全量结果属于此前五类决策版本的历史验收。

2026-10-14 可靠性修复验收：在服务器独立目录 `/tmp/guardian-reliability-4jyYsE`，使用既有 Python/Harbor 环境执行 `PYTHONPATH=src GUARDIAN_DOCKER_TESTS=1 .../python -m pytest -q`，结果为 **217 passed、3 skipped，114.39 秒**。Ruff 和 `git diff --check` 通过。覆盖大日志事件配对、半行与读取积压、轮转和损坏降级、600 秒合法等待、零调用纠正、多调用串行、结构化决策分流、调查失败继续观察、明确终止和原期限清理。搜索使用本地 HTTP 上游验证转发、真实凭据替换、请求参数保留和用量记录。

这些测试没有调用真实模型 API：真实上游是否支持搜索契约、守护模型的实际诊断质量仍需在线验收。此次未替换线上平台、未执行付费复跑；原始异常记录和候选清单保存在 `.cache/eval-80-review/`，其中 `rerun-candidates.json` 包含 39 个发生过 incident 的 Agent－题目组合，状态为未开始。

```sh
uv run pytest -q
GUARDIAN_DOCKER_TESTS=1 uv run pytest tests/test_guardian_docker.py -q
```

Docker 验收使用独立临时目录和唯一标签容器，包括无限输出、退出 shell 遗留子进程、协议直接恢复、守护失联兜底，以及兜底之后执行真实 Harbor 正/负对照题。需要本地已有 `python:3.12.13-slim-bookworm` 镜像；不会调用大模型 API。回归测试还覆盖取消、过期权限、动作预算、只读 API 和原有实验功能。

2026-10-12 验证记录：在 `100.93.173.10` 的隔离目录 `/tmp/agent-harness-guardian-cgwNw3i9` 中，使用既有 Python 3.12/Harbor 0.24.0 环境运行 `GUARDIAN_DOCKER_TESTS=1 PYTHONPATH=src .../python -m pytest -q`，**96 passed，81.55 秒**。真实 Harbor 队列测试使用单并发：第一题同步阻塞工作进程，使 Harbor 的异步超时也无法运行；父进程终止该题、保留 `GuardianTrialTerminated`，第二题正常得到 reward=1。另有四次正/负对照尝试全部按预期评分。新增守护模块和测试通过 Ruff 检查，两个修改的 JavaScript 文件通过 `node --check`。

首次 Docker 对照验收发现从 Windows 打包的 shell 脚本为 CRLF，导致 Linux shebang 无法执行；已添加 `.gitattributes` 固定 `.sh` 为 LF 后重跑通过。此次只操作临时验收容器，没有替换线上 BKCrab、运行付费评测或修改历史实验。
