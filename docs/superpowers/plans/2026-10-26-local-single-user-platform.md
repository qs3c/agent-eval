# 本地单用户 Harness 测评与 RSI 平台 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在保留现有 Harbor、原生 Agent 和证据能力的基础上，交付本地单用户的标准接入、公平测评、可诊断结果和 GitHub 驱动 RSI 闭环。

**Architecture:** 模块化单体；本机 Manager 统一接受 Web/CLI/RSI 命令；SQLite 保存操作状态，文件保存不可变证据；容器承担执行隔离；Source/Recipe/Release/Plan 分离。

**Tech Stack:** Python 3.12、现有锁定的 Harbor/Pydantic/FastAPI/SQLite、Docker、现有前端 JS、pytest；不以本计划为理由升级依赖，不新增 Redis、Kubernetes 或多租户服务。

**Spec:** [完整架构设计](../specs/2026-10-26-local-single-user-platform-design.md)，源码基线 `328baf8`。

## Global Constraints

- 本文件是持续更新的分阶段实施路线。P0 已完成；P1 按可独立验证的任务推进，复选框仅反映已经取得验证证据的交付。
- 执行技能以实际环境可用性为准；按用户授权推进实现，不委派代理、不部署服务、不运行付费模型实验。
- 保留工作区原有未提交修改，尤其 `resources/live_model.py`、`tests/test_relay_roles.py` 与 `docs/interview/`；将来实现角色策略时先核对这些改动是否已经合并。
- 新路径均相对仓库根目录。下面的新接口、文件和命令是计划，不表示当前已经存在。
- 默认测试用假模型、本地临时 Git 仓库和 fake executor；Docker 集成验收单独执行，真实 API/公开 GitHub 写操作须有对应授权与预算。
- 不改变原生 harness 循环来追求接口一致。所有测量条件、适配 patch 和角色策略版本化；缺失证据标 unknown。
- 同一作业只允许一个调度 owner；迁移不改写历史原始证据；新旧 schema 不并发写。
- 遵守 `AGENTS.md` 的中文 RSI 与中英双语 PR 规则；保持“选择测试记录即可创建案例”的交互。

---

## 阶段图与工作量使用方式

```text
P0 T01–T02
   → P1 T03 → T04 → T05 → T06
   → P2 T07 → T08 / T09 → T10 → T11
   → P3 T12 → T13
   → P4 T14 → T15
   → T16 完整验收与迁移发布
```

T07 依赖 T03/T04，不必等待所有结果页面完成；T09 依赖 T03/T05；T10 可在 T07 后与具体 Agent 集成分别推进。此处表示代码依赖，不要求多 Agent 并行执行。每项按“契约/失败用例→最小实现→迁移接线→验收”交付，不做跨阶段大爆炸重构。工期在 P0 完成并确认支持矩阵后按实际构建与验收成本估算，不用未经验证的天数承诺覆盖任意 Agent。

## P0：建立可重复的本地产品基线

**状态：已完成（2026-10-26）。** 实现 doctor、独立 Agent 准备入口和旧格式兼容夹具；完成独立 Python 环境安装、Linux Docker controls 与相关回归。完整命令、测试数量、原有改动保护和验收局限见 [P0 验证记录](../../p0-validation.md)。本阶段不提供新 schema 迁移；P1 尚未开始。

### T01 — 当前能力与安装诊断（R01、R12）

**文件：** 新增 `src/agent_harness_eval/doctor.py`、`tests/test_doctor.py`、`docs/local-deployment.md`；修改 `cli.py`、`README.md`、`scripts/prepare-native.sh`。

- [x] 记录当前 CLI/API、状态文件与 fixture；将 README 中历史某轮授权/固定模型试跑叙述标为历史记录，不作为新用户默认授权。
- [x] 定义 doctor 结果 `{check_id, required_by, status, evidence, remediation}`；按选择的 Agent/benchmark 检查依赖，不执行付费请求或安装未知脚本。
- [x] 实现 Linux/Docker/磁盘/进程与工具链检查；拆开 DSH/BKCrab 准备入口，使单独安装一种 Agent 不要求另一种依赖。
- [x] 编写推荐安装、升级、卸载、私网访问、数据备份和 WSL2 未认证说明；明确平台启动与 BKCrab 服务 Docker 部署的区别。
- [x] 测试：缺 Docker/低磁盘/仅 DSH 时无 Go/无模型凭据四类报告；使用现有 controls 完成一次无模型端到端验证。

**验收：** `uv run pytest -q tests/test_doctor.py`；在 Linux 验收机运行 `uv run agent-harness-eval doctor` 与现有 `run experiments/controls.json --output <新目录>`。记录实际输出，不把本计划中的命令当作已通过。

### T02 — 迁移夹具与兼容基线（R12）

**文件：** 新增 `tests/fixtures/legacy_workspace/`、`tests/test_legacy_workspace.py`、`docs/workspace-migration.md`；引用现有 `freezing.py`、`provenance.py`、`run.py`，暂不重写它们。

- [x] 从脱敏的小型控制实验构建旧 `.console`、RSI、复评和 artifacts 夹具，覆盖 completed/interrupted/partial-grade，不复制真实 key 或巨型日志。
- [x] 保存原始文件 hash 清单，写“只读导入不改原文件”“重复导入无新增记录”“缺失 provenance 显示 unknown”测试。
- [x] 记录当前 `workbench_repair` 允许范围和配置规范化行为，作为恢复回归测试输入。

实施说明：兼容测试调用现有 `Console.import_history()`、RSI Store 与复评读取接口；缺失来源保留缺失并阻止可比较结论，未知费用保持 null。SQLite 使用旧 schema 的文本快照在测试目录还原；故障状态由实际 controls 产物派生，不冒称实际跑过 RSI/SWE。新增 `tests/test_prepare_native.py` 验证脚本分支，CLI/RSI 命令模块改为延迟加载执行依赖，使 doctor 在缺 Harbor 或非 Linux 环境仍能给出报告。

**验收：** `uv run pytest -q tests/test_legacy_workspace.py`；迁移设计明确备份/恢复路径和新旧写入互斥。

## P1：可靠的实验内核

### T03 — 领域对象与本地状态库（R06、R12）

**状态：已完成。** 验证与边界见 [P1/T03 验证记录](../../p1-validation.md)。新增状态放在项目根下 `.workspace/`，避免污染源码根和覆盖既有 `.console/`；逻辑数据归属与设计一致。T04–T06 尚未完成，P1 不能标记为整体完成。

**文件：** 新增 `src/agent_harness_eval/workspace/{__init__,models,store,migrations,legacy}.py`、`tests/test_workspace_store.py`、`tests/test_workspace_migration.py`；修改 `config.py`、`freezing.py`、`manifests.py`。

- [x] 按设计定义 schema-versioned SourceRevision、AgentRelease 引用、BenchmarkRevision、ExperimentPlan、Attempt、GradeRun、ResourceLease、Incident；明确不可变输入与可变状态表。
- [x] 定义 canonical JSON/hash 规则：集合排序、任务与 Agent 顺序保留、排除 secret 值；用相等/不同输入测试冻结身份，覆盖已有 set 规范化问题。
- [x] 实现 SQLite 事务、唯一幂等 key、事件 outbox、schema migrations 和 dry-run；记录 raw artifact 引用，不复制大日志入库。
- [x] 导入 T02 fixture，备份后迁移；模拟失败并验证事务回滚与备份可恢复，再重复导入。失败时优先 SQLite rollback 保留原数据库，不替换活动 DB 文件；独立备份验证保存原 schema 与数据。

实现补充：`workspace-import` 默认 dry-run；旧格式导入和 schema 升级分别原子化。实际测试覆盖活动 owner 锁、旧 WAL、新版 schema、符号链接索引、失配幂等 key、丢失引用、未知来源，以及导入前后原 SQLite/JSON/SQL 哈希。`config.py`、`freezing.py`、`manifests.py` 提供兼容辅助接口；没有自动切换现有运行器。

**核心契约示例：** `store.create_plan(frozen_plan, idempotency_key) -> plan_id`；重复相同输入返回原 ID，相同 key 不同输入报冲突，不覆盖。

**验收：** `uv run pytest -q tests/test_workspace_store.py tests/test_workspace_migration.py tests/test_legacy_workspace.py`；原始夹具 hash 完全不变。

### T04 — 单一命令服务与管理进程（R01、R10、R12）

**文件：** 新增 `src/agent_harness_eval/operations/{__init__,commands,manager,worker,recovery}.py`、`tests/test_operation_commands.py`、`tests/test_manager_recovery.py`；修改 `console.py`、`cli.py`、`web.py`、`run.py`、`rsi/api.py`。

- [ ] 实现统一 `submit/pause/resume/cancel/query` 服务；Web/CLI/RSI 只交命令，不再各自创建实验运行队列。
- [ ] Manager 通过 executor façade 调用现有 `execute`；实现本机唯一锁、heartbeat、generation fencing 和启动 reconcile。
- [ ] 将 Web 生命周期与 Manager 分离；增加启动/状态/停止入口，停止 Manager 的行为明确为排空或取消，不等同浏览器关闭。
- [ ] 暂停不派新 attempt，取消回收自有资源；恢复保留已完成 attempts，只重试确认可重试作业。
- [ ] 测试中杀 Worker/Manager、延迟旧 Worker 回包、模拟外部请求结果未知；必须拒绝旧 generation 写入并展示 reconciliation 状态。

**验收：** `uv run pytest -q tests/test_operation_commands.py tests/test_manager_recovery.py`；关闭 Web 后 fake 长任务继续、重启后已完成 attempt 执行计数仍为 1。旧入口在迁移开关下不能同时消费同一作业。

### T05 — 总资源预算、消费者租约与分批背压（R10、R12）

**文件：** 新增 `operations/{resources,retention}.py`、`tests/test_resource_admission.py`、`tests/test_resource_recovery.py`；修改 `batching.py`、`image_lifecycle.py`、`regrading.py`、`guardian/docker.py`、`guardian/retention.py`。

- [ ] 统一 eval/grade/build/platform-agent 的主机资源账本，迁移现有同用户复评限制；所有入口使用同一准入层。
- [ ] 资源记录实际 ID、owner 与消费者；acquire/release 幂等，带 fencing；接管前核验进程/容器仍存活。
- [ ] 批次增加磁盘估算/headroom/评分积压检查，限制预取窗口；保留按数量分批作为上限。
- [ ] 添加周期 TTL/配额 GC；归档失败、清理失败、空间不足可重试且不影响已确认评分。
- [ ] 模拟 500 任务、低磁盘、评分阻塞、外部镜像引用、重复释放和进程重启；仅回收无消费者且确认自有资源。

**验收：** `uv run pytest -q tests/test_resource_admission.py tests/test_resource_recovery.py`；fake 运行记录最大在途资源不超配置、准备数量有界、外部资源删除数为 0。再执行已有镜像清理相关测试，覆盖 Compose 实际镜像与旧 hb 镜像。

### T06 — 评分作业与统一结果读模型（R06、R10）

**文件：** 新增 `src/agent_harness_eval/measurement/{__init__,results,comparability}.py`、`tests/test_measurement_results.py`、`tests/test_grade_jobs.py`；修改 `regrading.py`、`report.py`、`comparison.py`、`console.py`、`static/{app,comparison}.js`。

- [ ] 将官方复评接入 Manager 作业，保留现有哈希校验与瞬态重试；GradeRun 绑定补丁 hash 和 scorer 版本，不覆盖旧结果。
- [ ] 汇总 execution/scoring/evidence/resources 四轴；SWE 默认主结果来源为指定官方复评，Harbor 内置分数仍独立可查。
- [ ] 展示计划/执行/已评分分母、未知费用比例、逐题重复和错误；部分评分不输出伪全量通过率。
- [ ] 比较不兼容时列出条件差异；禁止 best-of 与静默删除 assisted 样本。
- [ ] 验证一次评分故障重试只增加 GradeRun 执行计数，不增加 Agent 执行计数；unresolved 不触发基础设施重试。

**验收：** `uv run pytest -q tests/test_measurement_results.py tests/test_grade_jobs.py`；页面 fixture 覆盖待官方评分、评分失败、未解出、缺失证据和清理失败的不同展示。

## P2：标准接入与可比较实验

### T07 — Recipe、Release 与 conformance（R02、R08）

**文件：** 新增 `src/agent_harness_eval/integrations/{__init__,schema,builder,conformance,store}.py`、`agents/recipe.py`、`tests/test_integration_recipe.py`、`tests/test_release_build.py`、`docs/agent-integration.md`；修改 `runtime_bundle.py`、`provenance.py`。

- [ ] 定义版本化 recipe schema 与 CLI/Python 共同输入输出契约；文档提供完整小型示例，使用 argv 和 secret reference。
- [ ] 实现 clean source → eval copy → patch → build → artifact digest；原始源码保持不变，patch 冲突停止，不自动跳过。
- [ ] Runnable/Comparable/Diagnosable 检查独立存证，能力缺失不能凭声明升级认证。
- [ ] 构建隔离不挂宿主凭据或 Docker socket；受信构建器在隔离边界外管理 Docker。
- [ ] 测试同 SHA 不同 patch/profile 产生不同 Release，dirty upstream 拒绝被当作 clean SHA，构建输出差异保留不同身份。

**验收：** `uv run pytest -q tests/test_integration_recipe.py tests/test_release_build.py`；接入一个 fixture CLI Agent，无需改核心分发便能完成 controls。

### T08 — 内置 Agent 目录与独立安装（R03）

**文件：** 新增 `integrations/recipes/{dsh,bkcrab,opencode,hermes}.yaml`、`tests/test_builtin_recipes.py`、`docs/builtin-agents.md`；修改 `agents/{dsh,bkcrab}.py`、`dsh_bundle.py`、`scripts/prepare-native.sh`，按需要新增专用构建脚本。

- [ ] 先迁移 DSH/BKCrab，保留 native loop、DSH Standard 的真实 preset 和 BKCrab eval patch；做迁移前后控制任务行为对照。
- [ ] 实施时查阅各上游官方仓库，锁定当时可验证的版本/依赖/许可证与实际入口；本计划不猜测 OpenCode/Hermes 的启动参数。
- [ ] 分别接入 OpenCode/Hermes，记录 profile 差异和子模型覆盖情况；未通过的条目显示 unavailable 与原因。
- [ ] 每种 Agent 单独完成 mock/隔离/取消/证据/模型协议检查，独立安装和卸载不破坏其他条目。

**验收：** `uv run pytest -q tests/test_builtin_recipes.py`；四种各有真实运行证据与兼容矩阵。某一种失败不阻止其他已验收 Agent 发布，但不得把 P2 四基线目标标成全部完成。

### T09 — Benchmark 生命周期与选择冻结（R04）

**文件：** 新增 `benchmarks/{base,registry,harbor_tasks,selection}.py`、`tests/test_benchmark_registry.py`、`tests/test_task_selection.py`、`docs/benchmark-integration.md`；修改 `benchmarks/swe.py`、`config.py`、`batching.py`。

- [ ] 定义 resolve/materialize/prepare/collect/grade/release 契约，返回可被 ResourceLease 管理的依赖。
- [ ] SWE 插件封装现有 export/official regrade；一般 resolved 指标不再隐式触发 SWE。
- [ ] controls 与标准 Harbor 自定义任务走同接口；多步骤/独立 verifier/模拟用户能力不足则提交前拒绝。
- [ ] 实现 dataset/version/split、seed 抽样、历史任务排除；冻结最终任务列表和内容 hash。
- [ ] 检查隐藏答案与 verifier 不进入 Agent 可读 workspace/prompt；导出全部重复尝试，不挑 best-of。

**验收：** `uv run pytest -q tests/test_benchmark_registry.py tests/test_task_selection.py`；seed 重放、排除交集、数据内容改变、能力不支持有明确断言；保留已有 SWE export/评分测试。

### T10 — 模型角色与公平策略（R05）

**文件：** 新增 `src/agent_harness_eval/policies/{__init__,model,network,budget}.py`、`tests/test_model_policy.py`、`tests/test_model_role_routing.py`；修改 `model_pool.py`、`resources/live_model.py`、`config.py`、`search.py`、`resources/search_review.py`、`static/models.js`。

- [ ] 合并前核对现有未提交 role 改动，避免覆盖；将可信角色绑定到服务入口，禁止请求体选择高权限角色。
- [ ] 固定模型池快照；统一被测主/子/压缩/回退模型配置，平台模型与 benchmark simulator/grader 分开路由和计费。
- [ ] 验证协议、参数兼容与真实使用证据；不支持的 Agent 无法取得 Comparable，不静默回退其他模型。
- [ ] 把网络/搜索/JEV 版本与阈值纳入指纹；保留 deny/unavailable/provider-failure 区别及审查覆盖限制。
- [ ] 预算独立限制平台和被测调用；缺价格或 usage 时费用未知，硬上限无法保证时明确拒绝“保证不超额”模式。

**验收：** `uv run pytest -q tests/test_model_policy.py tests/test_model_role_routing.py`；假服务记录子调用/压缩/回退实际模型；恶意 purpose 字段不能改变角色；模型池修改不影响已冻结计划。

### T11 — 从接入到对比的最小产品流程（R01–R06）

**文件：** 新增 `tests/test_local_comparison_flow.py`；修改 `web.py`、`console.py`、`static/app.js`、`static/comparison.js`、`README.md`、`docs/local-deployment.md`。

- [ ] UI 连成模型配置→选择/导入 Agent→接入检查→benchmark/子集→实验预检→运行→逐题对比。
- [ ] 预检显示构建、下载、磁盘、付费和模型覆盖缺口；密码字段提交后仅显示引用状态。
- [ ] 用户能定位不可比较原因、未知费用和官方评分等待，无需读服务日志猜状态。
- [ ] 做自己的 fixture Agent 与已验收内置 Agent 的同模型端到端回归，保留完整 evidence 链。

**验收：** `uv run pytest -q tests/test_local_comparison_flow.py`；Linux 干净安装按文档完成一次实际控制实验。P2 产品可以独立发布，不等待 RSI 自动化。

## P3：可信诊断与泛化改进

### T12 — 共享平台 Agent 执行器（R11）

**文件：** 新增 `src/agent_harness_eval/platform_agents/{__init__,protocol,executor,roles}.py`、`tests/test_platform_agent_executor.py`；修改 `guardian/{driver,model_driver,supervisor}.py`、`rsi/executors.py`。

- [ ] 定义 `execute(role, evidence_refs, workspace, model_policy, tool_policy, deadline) -> structured_result + raw_trace`，保留现有 driver 兼容。
- [ ] 为一个已验收成熟 Agent 实现完整多轮执行适配；每个角色使用独立权限/状态，不共享被测 Agent 的用户 home。
- [ ] 通过确定性层校验 exec/资源处置，超时/取消/清理不依赖模型配合；按资源 ID 核验作用对象。
- [ ] 使用多轮 fake harness 测试调查→工具→观察→结论，并验证越权动作、预算耗尽与中断处理。

**验收：** `uv run pytest -q tests/test_platform_agent_executor.py`；Guardian 不得直接写被测解题代码；assisted 动作使实验模式显式变化，无法混入 autonomous 指标。

### T13 — RSI 分层验证与产品/适配分离（R06、R08、R09）

**文件：** 新增 `rsi/validation.py`、`tests/test_rsi_validation_levels.py`、`tests/test_rsi_release_isolation.py`；修改 `rsi/{models,native_build,native_evaluation,evaluation,regression,publication,runner}.py`、`static/rsi.js`、`docs/rsi.md`。

- [ ] 将 BKCrab 特定构建转为 T07 recipe 调用，旧 profile 通过兼容适配器保留；不同 Agent 使用同一候选/验证模型。
- [ ] 分开 upstream candidate worktree 与 eval 派生目录；PR diff 检查禁止不相关的 integration patch 混入。
- [ ] 验证记录明确 build、targeted、benchmark-replay、holdout 各自结果、输入 SHA、recipe 与模型策略。
- [ ] baseline/candidate 同测试和正负对照；编译失败/超时不能证明假设；必要时桥接旧源码+新 recipe。
- [ ] 保持中文诊断主题、冻结原证据和双语 PR；验证不足的草稿明确局限，不自动声称提升或合并。

**验收：** `uv run pytest -q tests/test_rsi_validation_levels.py tests/test_rsi_release_isolation.py`；fixture 可复现一处 harness bug、验证修复、生成只含产品改动的双语 PR 草稿；测试有 targeted pass 但 replay fail 的拒绝提升结论路径。

## P4：GitHub 驱动版本与闭环

### T14 — 来源跟踪与 Integration Agent（R02、R07、R08）

**文件：** 新增 `integrations/{sources,tracking,assistant}.py`、`tests/test_source_tracking.py`、`tests/test_integration_repair.py`；修改 `operations/manager.py`、`web.py`、`cli.py`；新增 `docs/source-tracking.md`。

- [ ] 接受 URL/ref，固定 SHA；优先标准 recipe，缺失时调用 T12 Integration 角色生成候选并执行 conformance。
- [ ] 本机轮询/手动刷新、退避、连续更新合并与 repo/ref/SHA 幂等；离线恢复只查询当前状态。
- [ ] 维护 latest observed / latest admitted / recommended 三指针，默认在轮次边界采用新准入版本。
- [ ] patch 冲突保留失败状态并生成 repair recipe 候选；旧 Release 可用但不冒充最新。
- [ ] 使用本地 Git 仓库模拟普通 push、force-push、ref 删除、连续三次更新、patch 失配及重启。

**验收：** `uv run pytest -q tests/test_source_tracking.py tests/test_integration_repair.py`；当前运行 plan digest 不变，新 SHA 只进入下一轮；同一 SHA 不重复创建来源事件/Release 准入作业。

### T15 — PR 合并观察与下一轮测评（R07、R09）

**文件：** 新增 `rsi/{merge_tracking,cycle}.py`、`tests/test_rsi_merge_cycle.py`；修改 `rsi/{git,publication,runner}.py`、`operations/manager.py`、`static/rsi.js`。

- [ ] PR 状态查询抽象成只读/发布接口；使用 case/candidate/base 幂等 key，未知发布结果先查询，不直接再创建。
- [ ] base 漂移使验证过期；更新 candidate 后重新运行配置要求的验证，保留旧证据。
- [ ] merge/squash 后解析真实目标 SHA，走与普通 push 同一来源准入流程；禁止复用 merge 前产物冒充新 Release。
- [ ] 下一轮绑定新 Release 与同条件基线，记录质量结果；退化不更新 recommended，latest admitted 仍如实显示。
- [ ] 用户预算/轮次上限/等待 merge/接入失败均可停止循环并给出原因；默认不自动 merge。

**验收：** `uv run pytest -q tests/test_rsi_merge_cycle.py`；fake GitHub 覆盖 merge commit、squash、外部 push、关闭未合并、base drift、响应丢失和预算耗尽。无公开仓库写入即可验收状态机。

## T16：迁移、故障演练与发布（R01–R12）

**文件：** 新增 `tests/test_platform_lifecycle.py`、`docs/local-platform-validation.md`；更新 `README.md`、`docs/{workspace-migration,local-deployment,agent-integration,benchmark-integration,source-tracking,rsi}.md`。

- [ ] 对照需求表逐项链接实际测试、日志和限制，不把计划勾选当测试证据。
- [ ] Linux Docker 上实际完成 controls、一个标准 Agent pair、SWE 小样本导出/复评、取消恢复和资源清理；真实模型实验另按授权进行，不是无模型单测的隐含步骤。
- [ ] 对 20/80/500 任务分别做 fake 大批次调度测试，并以小规模实际镜像/容器测试核验资源预算假设；不宣称 fake 结果证明 500 题真实容量。
- [ ] 做低磁盘、评分器故障、模型故障、Manager crash、遗留容器、recipe 冲突、GitHub 断网、备份恢复演练。
- [ ] dry-run 导入旧工作区、正式导入、重复导入与回滚；旧 evidence hash 不变，不能有双重执行 owner。
- [ ] 更新能力矩阵、已知限制、配置迁移与升级步骤；移除不再使用的旧调度仅在所有入口验收通过后进行。

**最终门槛：** 文档能让新用户独立完成“本地安装→自己的 Agent→同模型基线→benchmark→结果诊断”；自动化闭环还能完成“普通 push 或 RSI merge→新 Release→下一轮对比”，中途故障不丢失原始证据、不无界消耗资源、不伪造已验证结论。

## 跨任务接口约定

以下为目标 Python 接口草案；各任务以此协作，实现时如调整签名，必须同步调用方和契约测试。`*Ref` 是包含 ID/digest 的类型化引用，不能携带 secret 明文；`Context` 包含 workspace、deadline 与可信 owner。类型集中在所属模块，不创建无边界的全局 utilities。

| 提供任务 / 文件 | 接口 | 消费方与约束 |
| --- | --- | --- |
| T03 `workspace/store.py` | `create_plan(plan: ExperimentPlan, idempotency_key: str) -> PlanRef` | T04；不可变输入冲突显式失败 |
| T04 `operations/commands.py` | `submit(plan: PlanRef) -> RunRef`；`control(run: RunRef, action: ControlAction) -> CommandRef` | Web/CLI/RSI；ControlAction 只接受 pause/resume/cancel |
| T04 `operations/recovery.py` | `reconcile(workspace: WorkspaceRef) -> RecoveryReport` | Manager 启动；列出确认存活/结束/未知的 owner，未知不直接重跑 |
| T05 `operations/resources.py` | `acquire(request: ResourceRequest, owner: OwnerRef) -> LeaseDecision`；`release(lease: LeaseRef, generation: int) -> ReleaseResult` | build/eval/grade/platform-agent；Decision 区分 granted/wait/rejected 与原因 |
| T06 `measurement/results.py` | `summarize(run: RunRef, scorer: ScorerRef) -> RunSummary` | 页面、比较、RSI；显式包含分母、覆盖率和四轴状态 |
| T07 `integrations/builder.py` | `build(source: SourceRevision, recipe: IntegrationRecipe, context: BuildContext) -> BuildResult` | 内置目录、URL 接入、RSI；失败没有可运行 Release |
| T07 `integrations/conformance.py` | `check(release: AgentRelease, policy: ConformancePolicy) -> ConformanceReport` | T08/T10/T14；逐项证据，不返回一个无解释布尔值 |
| T09 `benchmarks/base.py` | `resolve(spec: BenchmarkSpec) -> BenchmarkRevision`；`prepare(task: TaskRef, context: ExecutionContext) -> PreparedTask`；`collect(attempt: AttemptRef) -> ArtifactSet`；`grade(artifacts: ArtifactSet, scorer: ScorerRef, context: ExecutionContext) -> GradeResult` | Manager；materialize 将冻结定义落盘，release 释放实际资源消费者；评分失败与 unresolved 分开 |
| T10 `policies/model.py` | `freeze(profile: ModelProfile, role: ModelRole) -> ModelPolicy`；`verify_usage(policy: ModelPolicy, evidence: UsageEvidence) -> PolicyReport` | 构建检查、attempt、平台 Agent、比较；未知调用不能判一致 |
| T12 `platform_agents/executor.py` | `execute(request: PlatformAgentRequest) -> PlatformAgentResult` | Guardian/Integration/RSI；request 封装角色、证据、目录、模型/工具策略与 deadline |
| T13 `rsi/validation.py` | `validate(candidate: CandidateRef, policy: ValidationPolicy) -> ValidationReport` | 发布与循环；报告按验证层、baseline/candidate 输入身份组织 |
| T14 `integrations/tracking.py` | `refresh(source: TrackedSource) -> SourceObservation` | 手动/轮询/merge；相同 repo/ref/SHA 复用事件 |
| T15 `rsi/merge_tracking.py` | `observe(pr: PullRequestRef) -> MergeObservation` | cycle；返回真实目标 SHA 或未合并状态，不使用 candidate SHA 代替 |

最小故障测试应断言业务不变量，而不是只断言函数调用：

```python
# T04/T06 的测试形状：具体 fixture 由实现任务提供。
run = fixture.submit_two_attempts()
fixture.finish_agent(run, attempt=1)
fixture.fail_grading_once(run, attempt=1)
fixture.restart_manager()
fixture.drain_queue()
assert fixture.agent_execution_count(run, attempt=1) == 1
assert fixture.grade_execution_count(run, attempt=1) == 2
assert fixture.summary(run).scoring == "complete"
assert fixture.original_plan_digest(run) == fixture.current_plan_digest(run)
```

```python
# T14/T15 的测试形状：普通 push 与 merge 进入同一准入路径。
active = fixture.start_round(source_sha="A")
fixture.push_source("B")
fixture.refresh_source_twice()
assert fixture.plan_source_sha(active) == "A"
assert fixture.admission_job_count("B") == 1
fixture.fail_patch_check("B")
assert fixture.latest_observed_sha() == "B"
assert fixture.latest_admitted_sha() == "A"
assert fixture.source_status("B") == "integration_failed"
```

## 每个任务的提交与验证约定

- [ ] 先运行该任务目标测试与直接受影响的现有测试；出现失败先确认基线，再修复，不反复跑无关全量测试。
- [ ] 模块边界或 schema 变化完成时执行完整 `uv run pytest -q`；Docker/真实 API 验收单独记录环境和范围。
- [ ] `git diff --check` 通过，检查无凭据、无 artifacts、无其他人的工作区改动进入提交。
- [ ] 提交说明写清行为、迁移、验证事实与局限；未执行的测试明确标注。每个阶段保留可回退点，不推送或部署未经授权的外部变更。
