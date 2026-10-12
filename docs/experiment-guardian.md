# 实验运行守护

固定监测程序负责发现疑似停滞、先留存现场、启动守护 Agent。守护 Agent 自主分析并直接执行目标容器中的任意 shell；没有故障类型到修复动作的规则表。独立父进程控制期限、保存实际操作和观测结果；无法恢复的题目保留失败，释放并发槽位，继续后续题目。

这是 RSI 的运行期证据入口。本版不包含修改 harness 源码、创建 PR、候选版本复评或 DGM 多代搜索；也未指定首个模型 Agent。协议测试使用确定性脚本，不代表模型诊断能力。

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
    "idle_seconds": 300,
    "confirmations": 2,
    "tool_grace_seconds": 45,
    "diagnostic_seconds": 120,
    "verification_seconds": 45,
    "command_seconds": 20,
    "cleanup_seconds": 30,
    "max_incidents": 3,
    "max_actions": 12
  }
}
```

`command` 是 argv 数组，不经过宿主机 shell 展开。工作目录是本次 incident；脚本路径使用绝对路径，或使用已安装模块 `python -m package`。`version` 必填；应固定适配器、模型和提示词版本。凭据通过 `pass_env` 白名单传递，不放在参数或配置中。适配器是管理员安装的可信宿主机程序，不是安全沙箱；容器工具接口只定位当前题。平台会记录经协议调用的操作，适配器应通过这些接口进行介入，不能自行绕过日志通道操作宿主机。

守护模型费用与被测 Agent 费用是不同项目；当前结果中的 token/USD 仍只代表原评测统计。适配器可将自己的用量写入 `finish.arguments.usage`，未提供时未知，平台不将其猜测为零。启用 assist 前需要自行配置适配器的提供商与用量限制；本版没有隐式默认模型。

## 发现与期限

- 定时读取 Harbor 生命周期：创建、环境启动、Agent 开始/结束、验证和清理。
- 读取有限原生日志尾部与进程快照。日志没有进展连续达到 `confirmations` 次才触发调查；stderr 输出和 CPU 忙碌不算有效进展。
- BKCrab `events.jsonl` 的 exec 调用/返回用于检测逾期工具；明确配置的工具 timeout 优先，Agent ID 为 `bkcrab` 且未提供 timeout 时使用当前 harness 的 120 秒默认值。其他 Agent 无对应埋点时依赖日志进展与阶段期限。日志尾部截断、遥测缺失都意味着信息不完整，不等同于已证明死锁。
- Agent 期限使用 Harbor 实际解析后的 timeout。守护操作在原期限到达或阶段结束后失效；允许 Harbor 在 `cleanup_seconds` 内结束取消和清理，随后独立强制兜底。守护不会增加题目的作答预算。
- `setup_seconds` 默认 1200，为创建/环境等阶段的外层兜底；`verifier_seconds` 默认 1200，为验证阶段外层上限。它们不改写 task.toml 的评分器超时。诊断、单次命令和恢复验证各有独立期限。
- 守护声称 recovered 只保存为判断；父进程仍观察原 Agent 日志进展、待返回工具和生命周期。界面用 `progress_observed`/`phase_advanced` 等事实描述，不声称已经证明根因被修复。
- 兜底先终止该题工作进程，再清理精确 Compose project 下的容器和网络。失败保留 `resources_released: false`，不会谎报清理成功。SIGTERM/SIGINT 正常取消走清理流程；宿主机关机、SIGKILL 或 Docker daemon 故障不受本进程保证，仍需已有的控制台 interrupted/recover 流程。

## JSONL 插件协议 v1

父进程首先向 stdin 写入一行 JSON：`protocol: eval-guardian/v1`、incident 绝对路径、工具名、动作上限和任务约束。适配器先读取目录中的 `incident.json`、`initial.json`、`host.json`，再向 stdout 一行一个请求，诊断文本写 stderr 或 finish，不能混入协议 stdout。

```json
{"id":"inspect-1","tool":"snapshot","arguments":{}}
{"id":"act-1","tool":"exec","arguments":{"command":"ps -eo pid,ppid,stat,comm"}}
{"id":"done","tool":"finish","arguments":{"summary":"证据、判断、做过的操作与残余不确定性","assessment":"recovered"}}
```

每个 id 唯一。snapshot/exec 请求会在 stdin 得到 `{"id":"...","result":{...}}`。exec 为目标容器 root shell，有独立进程组、容器内超时以及各 64 KiB 的 stdout/stderr 上限；超量输出继续被读取丢弃，避免管道阻塞或无界写盘。finish 保存总结并结束会话，不需要等待响应。无效 JSON、超长协议行、失联、超时或耗尽动作预算都留下结果并终止驱动进程组；无法观察到恢复时终止本题。

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
| after.json / outcome.json | 后续现场和平台观测到的进展/结束/终止结局 |

`/proc` 采集只读固定进程元信息及 FD 链接，不递归扫描进程文件，不读 pagemap、内存或环境变量。原始日志可能含私有任务内容，保留在原实验权限范围中。快照以首次写入为准，后续采集另存；不是防管理员篡改的审计存储。历史人工调查记录不会被伪装成自动采集；已有 `human-intervention.json` 的辅助标记可读取，但无法追补已销毁现场。

运行详情新增“实验运行守护”，支持事件列表、诊断摘要与命令记录。只读接口：`GET /api/runs/{id}/incidents` 和 `GET /api/runs/{id}/incidents/{incident_id}`；公开视图不返回日志正文、原始快照或模型推理。原始奖励不改写，报表/逐题比较显示外部辅助；跨批次检查守护策略与介入标记，不能把受辅助结果解释为纯自主成绩。

## 验证

```sh
uv run pytest -q
GUARDIAN_DOCKER_TESTS=1 uv run pytest tests/test_guardian_docker.py -q
```

Docker 验收使用独立临时目录和唯一标签容器，包括无限输出、退出 shell 遗留子进程、协议直接恢复、守护失联兜底，以及兜底之后执行真实 Harbor 正/负对照题。需要本地已有 `python:3.12.13-slim-bookworm` 镜像；不会调用大模型 API。回归测试还覆盖取消、过期权限、动作预算、只读 API 和原有实验功能。

2026-10-12 验证记录：在 `100.93.173.10` 的隔离目录 `/tmp/agent-harness-guardian-cgwNw3i9` 中，使用既有 Python 3.12/Harbor 0.24.0 环境运行 `GUARDIAN_DOCKER_TESTS=1 PYTHONPATH=src .../python -m pytest -q`，**96 passed，81.55 秒**。真实 Harbor 队列测试使用单并发：第一题同步阻塞工作进程，使 Harbor 的异步超时也无法运行；父进程终止该题、保留 `GuardianTrialTerminated`，第二题正常得到 reward=1。另有四次正/负对照尝试全部按预期评分。新增守护模块和测试通过 Ruff 检查，两个修改的 JavaScript 文件通过 `node --check`。

首次 Docker 对照验收发现从 Windows 打包的 shell 脚本为 CRLF，导致 Linux shebang 无法执行；已添加 `.gitattributes` 固定 `.sh` 为 LF 后重跑通过。此次只操作临时验收容器，没有替换线上 BKCrab、运行付费评测或修改历史实验。
