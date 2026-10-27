# 本地平台整体验收记录

日期：2026-10-26。目标是 Linux Docker 上本地单用户工作区。没有部署公共服务、改动已有 BKCrab 服务、调用付费模型、推送产品源码或发布公开 PR。

本轮验证在 `100.93.173.10` 的独立 `/tmp/harness-p3-cJ72` 源码与临时工作区中进行；Python 3.12、Harbor 0.24.0，使用已有固定基线 Release。Windows 本机用于编辑、Python 语法检查和 Node UI 测试，不据此声称 Windows 原生 Docker 支持已经验收。P0–P3 的历史记录分别在 [P0](p0-validation.md)、[P1](p1-validation.md)、[P2](p2-validation.md)、[P3](p3-validation.md)。

## 本轮已取得的证据

| 日志 / 测试 | 结果 | 证明的范围 |
| --- | --- | --- |
| `p4-contracts.log` | 25 passed、2 skipped | 来源状态、候选校验、共享平台执行器和产品流程契约；跳过项不是 Docker 通过证据 |
| `p4-cycle-tests.log` | 19 passed，63.18 秒 | 实际本机 Git→Manager→Docker build→Harbor conformance；坏版本保留旧 Release；fake GitHub merge/squash/base drift/关闭/响应丢失 |
| `p4-final-docker.log` | 22 passed，141.84 秒 | 新 Release 与冻结基线的实际四次复评；模拟分数不更新推荐；Integration readonly 源码、模型角色及预算拒绝 |
| `p4-lifecycle.log` | 5 passed，18.53 秒 | 20/80/500 假任务队列完整排空、唯一 owner、资源上限、重开状态不重放、静止双库备份恢复、低磁盘拒绝 |
| Node UI 回归 | 30 passed | RSI 案例/候选/模型池与实验列表既有交互保持；运行时使用 `.cache/rsi-ui/node_modules` 中的 jsdom |
| `p4-final-fixes.log` | 43 passed，148.90 秒 | 修正 API 拒绝码断言；上游重试占用同一请求预算、禁用 Release 阻止新轮次；重复真实 Docker 隔离/复评 |
| `p4-manager-fixes.log` | 18 passed，2.30 秒 | 独立官方评分冻结输入并进入 Manager、输入篡改拒绝、来源首建并发与现有恢复契约 |

首轮全量 `p4-full.log`：534 passed、3 skipped、1 failed，1012.60 秒。唯一失败是新增 Web 测试将既有 API 的 422 拒绝码误写为 400，修正后的专项 43 项已通过。最终独立全量 `p4-full-final.log`：**540 passed、3 skipped、0 failed，1023.58 秒**。跳过项是 `test_rsi_executors.py:210` 的两项需单独启用的旧原生 Docker smoke，以及 `test_search_review.py:264` 缺少提取后的冻结 DSH runtime/Node；不计作通过。Harbor 的 `Task.checksum` 有四条弃用警告。

初次真实来源准入失败定位到 Worker 分发局部名称遮蔽，已修复并由上述真实 Docker 测试重验。初次大批次生命周期 fixture 错把同工作区的多个 operation 当成可同时 claim；改为验证真实单 owner 契约，任务并发预算另由既有 `test_resource_admission.py` 覆盖。Node 首次调用未提供已安装 jsdom 路径，设置 `NODE_PATH` 后 30 项通过。

SWE 初次隔离验收使用 `django__django-13741`、oracle/nop 对照，但固定镜像未缓存且服务器 Docker Hub 连接被拒绝；两次尝试均记录为执行错误、未评分，分数 null。失败输出保存在 `artifacts/p4-swe-validation`，没有覆盖或冒充成功。

随后核验公开 OCI index `sha256:c4c76e39029d47b01bdbcd2b78077c736467b616dc64c01ac62f6793068b7f27` 及其 Linux amd64 manifest `sha256:4cbaf1ac645650663a4ff49f9efc604b6effebdbfb26b64c0dd8a75d80514d93`，经镜像源拉取相同 digest。只修改独立任务副本的镜像引用并记录 platform-selection，原任务和失败实验保持不变。`artifacts/p4-swe-validation-fixed/report.json` 得到 oracle 1/1、nop 0/1，两边 errors=0、scored=1；每边导出一条 prediction。

官方 `swebench==4.1.0` 首次运行因固定 commit 的 GitHub requirements 读取失败而终止，失败日志保留。通过临时 localhost、仅允许公开 GitHub 的 CONNECT 传输恢复 TLS 连接后，**评分脚本、官方版本和测试逻辑未改动**。最终使用新 Manager 官方评分入口运行，`p4-official-final.log` 对应两个完成作业：

- `artifacts/p4-official-validation-final/oracle.p4-final-oracle.json`：submitted=1、completed=1、resolved=1、errors=0。
- `artifacts/p4-official-validation-final/nop.p4-final-nop.json`：submitted=1、empty_patch=1、resolved=0、errors=0；官方 harness 不对空补丁执行测试，不能把它写成 completed=1 或 unresolved=1。

本次 records JSON SHA-256 为 `22b8a2625fc97faec00fcad0caf1ffac9898e4fbdce132e8d57b79c8307f97c8`。来源未保存的数据集仓库 revision 继续为 unknown，不回填推测值。官方日志显示未停止容器为 0；镜像保留作缓存，没有全局清理。临时网络传输已关闭，本次 SWE 与官方评分工作区的 Manager 已 drain 并确认退出；既有服务没有停止或替换。

首次依赖下载中断留下的一条资源预留，经 owner PID 与启动时间核对、确认原进程已退出后定向释放；记录为 `p4-fixture-capacity-recovery.json`。未释放其他工作区的预留。

## 需求追溯

| 需求 | 实现与验证 | 边界 |
| --- | --- | --- |
| R01 本地单用户 | `doctor`、独立 Manager；`test_doctor.py`、`test_manager_recovery.py`、产品流程 Docker 测试 | 无多租户认证；只绑定本机或可信私网 |
| R02 标准接入 | Recipe/Release、CLI/Python adapter、`test_integration_recipe.py`、`test_integration_conformance.py`、接入修复测试 | 未知项目不保证零配置；需固定构建镜像和依赖 |
| R03 内置基线 | BKCrab/DSH/OpenCode/Hermes 实际 Docker 控制与策略 relay 测试，见 P2 | DSH 发布包与源码版本区分；可运行不等于完整可比 |
| R04 benchmark | registry、标准 Harbor/SWE、冻结抽样；`test_benchmark_registry.py`、`test_task_selection.py`、`test_swe.py` | 新数据/镜像需准备；SWE 内置和官方评分分开 |
| R05 同模型公平性 | 冻结策略、角色入口、预算、模型路由测试、四基线控制调用 | 主/子/压缩/回退经可信 relay 时固定；未证实全部旁路不存在，完整 Comparable 保守为 false |
| R06 结果诊断 | 执行/评分/证据/资源四轴，逐题对比、原始证据跳转；结果与 UI 回归 | 未知费用不填零；缺失评分不当作 unresolved |
| R07 来源驱动 | source tracking、实际 Docker 准入、merge cycle 测试 | 运行中 Plan 不变；轮次边界采用新 Release |
| R08 patch 演进 | 配方/补丁哈希、独立 eval 副本、过期接入候选拒绝、产品 diff 隔离 | repair 明确发起；不无限付费自重试 |
| R09 RSI 闭环 | T13 分层验证、已有幂等发布、只读 PR 观察、真实合并 SHA 准入、下一轮复评 | 不自动合并；后续诊断/发布显式发起；推荐仅限冻结条件 |
| R10 有界资源 | 20/80/500 模拟队列、跨工作区资源上限、评分积压/低磁盘、真实小规模 Docker | 不是 500 题真实吞吐或容量证明 |
| R11 成熟平台 Agent | 原生 BKCrab/DSH 多轮循环、Guardian 只读调查、独立角色与 Docker 预算验证 | 自定义可信命令不自动获得沙箱认证；平台运行时需单独安装 |
| R12 恢复迁移 | dry-run/apply/重复导入/事务回滚/备份、真实 Manager crash、stale generation、原证据 hash | 未确认外部结果不自动重放；回滚须先停止全部写入方 |

## 故障验证入口

`test_manager_recovery.py` 验证真实进程崩溃与重启；`test_grade_jobs.py` / `test_batching.py` 验证评分失败不重新执行已完成 Agent；`test_model_policy.py` / `test_platform_agent_executor.py` 验证请求耗尽、协议拒绝与超时；`test_resource_admission.py` 验证低磁盘和评分背压；`test_resource_recovery.py` / Docker cleanup tests 验证资源所有权、遗留容器与清理失败重试；`test_source_tracking.py` / `test_integration_repair.py` 验证 Git 离线、ref 删除、force-push 与过期配方；`test_workspace_migration.py` / `test_platform_lifecycle.py` 验证迁移回滚、备份恢复和原始证据保持。

旧收件箱和读投影保留，执行权收口到 Manager；不为移除兼容代码而破坏历史恢复路径。新用户操作顺序、安装条件、凭据配置与升级步骤见 [本地部署](local-deployment.md)、[模型策略](model-policies.md)、[Agent 接入](agent-integration.md)、[来源跟踪](source-tracking.md)和[迁移备份](workspace-migration.md)。

最终全量使用 Python 3.12 的 `python -m pytest -q -rs`，启用 `RSI_DOCKER_TESTS=1 GUARDIAN_DOCKER_TESTS=1 INTEGRATION_DOCKER_TESTS=1 CLEANUP_DOCKER_TESTS=1 BUILTIN_MODEL_POLICY=1`，并指定已有 `BUILTIN_RELEASE_ROOT=/tmp/harness-p1-zUBzJWtE`。没有启用付费模型。Windows 本机 `compileall` 和 `git diff --check` 通过；实施前的三个未提交文件 SHA-256 保持不变，没有纳入实施提交。

## 2026-10-27 首次使用实走结果

本轮在独立源码副本 `/tmp/harness-onboarding-e72h` 从锁定依赖安装开始，使用浏览器操作接入、模型设置、创建对比、逐题证据和 RSI。基础版本为 `633e3af`，叠加本轮修复；新建虚拟环境、数据库和 Release 目录。复用了机器上的 uv、依赖下载缓存和固定 Docker 镜像，不等于全新操作系统、空缓存安装认证。

| 步骤 | 实际结果 |
| --- | --- |
| 首次安装 | `uv sync --locked --python 3.12` 成功；doctor 检查通过。Linux x86_64 / Python 3.12.3 / Docker，Web 使用独立端口 18765 |
| 基线接入 | 页面安装固定 BKCrab 源码并构建，Release `aa25ebfd3e03…` |
| 自有 Agent | 文档离线示例新建 Git 仓库，按标准 Python 配方构建；运行 `b9a5b923ba7c433dbb315c81e8aedd9b` 两题均为 1，无异常 |
| 模型配置 | 页面添加 API URL、模型 ID、测试 Key 并绑定实验角色；使用私网本机假 API，未调用付费服务 |
| 同模型对比 | 自有测试 Agent Release `274fba1b338d…` 与 BKCrab；两题、各一次，运行 `1c7ed7eee6e446f9b01ba59d73c421d8` 四次均完成、均评分、无异常 |
| 对比与定位 | 自有 Agent 主分 0.5，BKCrab 为 1；`file-preserve` 分数 0 对 1。原始指标显示 `artifact=1`、`no_extra_files=0`，定位到额外调试文件 |
| 诊断材料 | 从逐题对比直接保存零分尝试，案例 `94bf061f948442d0ae72205f6de7e9ca`，只含一条选中记录；未要求填写目标 |
| 诊断展示 | 确定性本机执行器读取冻结 reward 证据 `e0034`，生成中文诊断并在独立源码副本移除调试文件；候选 `5818b34425f84e59903a573f40a4d805` 检查通过，页面明确显示尚未复评、未发布 |

该诊断执行器是流程验收 fixture，不是大模型推理质量证明，也没有通过公开 PR 或父子 benchmark 复评证明能力提升。对比使用的假 API 只验证统一模型路由；分数不用于比较真实 Agent 能力，费用缺失仍显示未知。

### 本轮修复

- 空实验列表增加接入、配置模型、创建对比三步入口；主要创建入口指向新流程。筛选无结果时可直接查看全部记录。
- 接入页先展示自有 Agent 与内置基线，再展示 GitHub 跟踪和 RSI 闭环；缺少输入时禁用核验、启动按钮。
- 支持文档示例的 offline 模式；空 Agent 选择明确拒绝，题目数随 benchmark 规模收敛，非 live 不选择模型。
- 模型配置提示容器内 localhost 的含义；预检提示回环地址，拒绝禁网任务直接调用真实模型。`controls` 保持禁网，不自动放宽任务或验证器策略。实走使用独立任务副本，仅允许本机测试端点。
- Manager live 策略不一致时给出停止、等待退出和重启步骤，避免只重启 Web 后仍无法执行。
- 运行详情随新尝试落盘刷新轨迹列表；不反复重置未变化的选择。
- 逐题对比增加“保存这次尝试为诊断案例”，覆盖正常退出但得分为零的尝试；说明原生诊断执行器需独立准备。
- 修复自定义 JSONL 事件缺少 `type/data` 导致转换器异常、整批终止的问题：无法标准化的事件保留原文和逐行警告，不伪造原生事件覆盖，也不阻断评分。
- 接入文档明确 `model.api_key_env` 是列表，补齐 Git 提交、离线示例、网络策略和诊断操作步骤。

### 失败证据与验证范围

早期失败记录没有覆盖：`fbcbae0ad7ed482492f0a721bc34291c`、`115bf8769234414a84b2c27f1edf9ef1` 涉及测试样例凭据字段用错、回环地址及禁网任务；`6f08af8815604f0688144c79f0764d48` 复现轨迹转换器异常。最终成功运行沿用修正后的固定 Agent Release，只更新平台转换器，没有靠删除日志绕开问题。

- 最终常规 Python 全量：**517 passed、22 skipped**，94.68 秒（`onboarding-full-final.log`）；跳过项要求额外 Docker 开关或预备产物，未计为通过。
- 开启 `BUILTIN_RELEASE_ROOT` 的产品流程及 Web/模型/Manager/RSI 专项：**56 passed**，46.43 秒（`onboarding-final-tests.log`），包含实际 Docker 自有 Agent + BKCrab 同模型控制对比。
- 轨迹及配方专项：**18 passed**（`onboarding-trace-tests.log`）。
- Node UI：**36 passed**，包含新增六项首次使用回归。
- 原有三个未提交文件 SHA-256 与实施前一致。未推送、未替换服务器既有服务、未发布 PR。

本轮没有重复 SWE 下载和官方评分、全部四种基线构建、真实模型诊断质量或空缓存安装认证，相关历史证据仍见上文 P0–P4。当前仍需改善的产品门槛是：SWE 数据与镜像准备、平台原生诊断执行器独立安装、未知 GitHub 仓库需要配方/适配。它们不应被表述为“只填 URL 即保证成功”。

收尾状态：完成浏览器诊断验证后，100.93.173.10 的 SSH 连续连接超时，本机 Tailscale 仍为 Running。因此本轮独立 Web（PID 2370198 / 端口 18765）、假 API（PID 2332547 / 端口 19878）及工作区 Manager 的停止未确认，可能仍在运行；没有对既有生产服务执行停止操作。恢复连接后应先核对 PID 命令行包含该工作区，再定向停止 Web/假 API，并对该 root 执行 manager stop，确认 alive=false。测试输出和案例保留用于复核。
