# P1 实施与验证记录

日期：2026-10-26。T03–T06 已实现；完整离线及 Docker 回归通过 **442 passed、3 skipped**。抽样记录冻结的后续修复单独追加验证；本文件区分各次快照，未进行生产部署。

## T03 交付

- 新增 `workspace/models.py`：SourceRevision、AgentRelease、BenchmarkRevision、ExperimentPlan、Attempt、GradeRun、ResourceLease、Incident 及独立四轴状态；canonical JSON 保留任务/Agent 顺序，仅规范化集合。
- 新增 `workspace/store.py`：不可变内容身份、幂等请求、引用校验、generation 防止旧状态写入、事务 outbox；读取 Plan 时核对 digest。
- 新增 `workspace/migrations.py`：schema v1、预览、升级前 SQLite backup、DDL 失败 rollback、新版 schema 拒绝；正常读取不重复升级。
- 新增 `workspace/legacy.py` 与 `workspace-import` CLI：默认只预览；显式导入旧记录及 manifest 对应 Plan；原始证据与旧数据库不修改，不把旧队列加入新 jobs。
- `config.py`、`freezing.py`、`manifests.py` 提供 canonical 配置与 manifest→Plan 辅助接口；没有改写现有执行/评分逻辑。
- 新状态位于项目根下 `.workspace/state.sqlite`，已加入 Git 忽略；备份在 `.workspace/backups/`。使用方法见 [迁移文档](workspace-migration.md)。

## 验证

在 Linux 服务器的独立目录 `/tmp/harness-p1-zUBzJWtE` 验证，使用 P0 锁定的 Python 3.12 环境，通过 `PYTHONPATH=$PWD/src` 明确加载本次源码。没有覆盖服务器原项目或启动部署服务；本阶段不调用模型、不跑新 Docker 实验。

```bash
export PYTHONPATH="$PWD/src"
LITELLM_LOCAL_MODEL_COST_MAP=True /tmp/harness-p0-JGgSwiv7/.venv/bin/python -m pytest -q
```

最终全量回归：**395 passed、11 skipped，68.79 秒**。本次新增 20 项测试，既有 375 项继续通过；跳过项沿用环境专用 opt-in 条件。

验证覆盖：

1. capabilities/requires 换序身份不变，任务/Agent 换序身份改变；已知明文凭据字段与 NaN 拒绝进入指纹。
2. 多线程重复请求只有一条 Plan 请求事件；同 key 不同输入拒绝；事件与状态同事务，失败不留下半条事件。
3. Attempt 必须引用已有 Plan；过期 generation 拒绝更新；篡改持久 Plan 会在读取时发现。
4. dry-run 不创建 `.workspace/` 或其他文件；T02 夹具导入生成 3 个 Plan，未知 task hash、源码版本仍未知；jobs 数量为 0。
5. 相同输入重复导入新增 0；原 JSON、SQL 与旧 SQLite 哈希不变。同身份内容改变时拒绝覆盖。
6. 在已插入部分 legacy/Plan 后注入故障，索引及事件事务整体回滚；重试成功。故障 schema DDL 回滚保留旧 schema/数据，独立 backup 也包含原数据。
7. 控制台锁、新 Manager 锁、单实验运行锁在用时拒绝导入；未知未来 schema、未 checkpoint 的 WAL、符号链接索引、旧 config 中的明文 key 均拒绝。

另在 `cli-workspace/` 夹具副本验证实际 CLI 的预览→首次 apply→重复 apply：未还原 SQL 时有 4 个文件记录，首次新增 4、第二次新增 0；预览后不存在 `.workspace/`。还原旧 SQLite 的完整 7 记录场景由自动化测试覆盖，二者不要混为同一计数。

原始日志：服务器 `t03-full-tests.log`、`cli-preview.json`、`cli-apply.json`、`cli-repeat.json`；本机缓存 `.cache/p1/` 保存传输与验证材料。临时目录不是生产数据目录。

## 边界与后续

本节记录 T03 交付时的边界；T04 之后的新增能力见下方独立记录。

导入是显式、一致性快照，不是热迁移或持续同步。用户需停止旧写入方；锁和 running/WAL 检查是防误操作保护，不会主动停服务。导入不启动实验、不重试 RSI、不重新评分。

代码使用 SQLite rollback 恢复失败事务，而不是盲目覆盖打开的 DB 文件；独立备份用于离线恢复。目录内可能留下新 schema 数据库、备份和 owner 锁文件，但不会留下失败导入的半批记录。不是删除整个 `.workspace/` 才算“回滚”。

新领域对象和 jobs 表只是 T04 的基础，当前 Web、CLI、RSI 仍使用既有执行生命周期。暂停/取消、Worker fencing、自动恢复、全机容量准入和官方评分统一视图必须经过对应后续任务验收后才能宣称可用。

本次保留 `resources/live_model.py`、`tests/test_relay_roles.py`、`docs/interview/` 原有未提交内容；实施前后 SHA-256 与 [P0 保护清单](p0-validation.md#原有未提交改动保护)一致，不加入本次提交。

## T04 管理进程验收

新增 `operations/commands.py`、`manager.py`、`worker.py`、`recovery.py`、`submission.py`，并接线 CLI、Web、RSI 与复评收件箱。统一命令支持幂等提交、暂停/恢复/取消、单 owner claim、Worker heartbeat 和 generation fencing；CLI/Web 输入绑定不可变 Plan，执行前复核任务和 Agent 文件。Manager 与 Web 生命周期分离，保留旧锁防止新旧执行器同时消费。使用方法见 [Manager 文档](local-manager.md)。

新增测试覆盖命令竞争、失配幂等请求、取消与完成竞争、过期回包、未知外部结果、PID 启动身份、真实 Manager 进程被杀、Web 生命周期结束后长任务继续、排空退出、在途尝试完成后暂停与恢复。扩展回归修正执行锁检查先于 live 策略检查的问题。真实暂停测试又发现正常暂停心跳被误判为重复暂停确认，已修复并增加回归；修复后的管理命令与恢复测试 **10 passed**。

实际 CLI→Manager→Worker controls：`artifacts/t04-controls`，4 次尝试，正对照 1.0、负对照 0.0，缺失和执行异常均为 0，费用未知。实际暂停/恢复/取消的最终通过记录在 `t05-manager-docker-retry.log`：

- `artifacts/t05-pause-1793002884`：并发 1，在途尝试结束后暂停；暂停期间完成数保持 1，恢复后完成数为 4，无重复尝试，主机预留全部释放。
- `artifacts/t05-cancel-1793002884`：确认本实验容器已创建后取消；状态 cancelled，自有容器剩余 0，预留全部释放。
- 验证用 Manager 排空退出。原服务器服务未停止、未更新，未调用任何模型 API。

首次暂停验证的失败证据保留在 `t05-manager-docker.log` 和 `artifacts/t05-pause-1793002391`。该次 Worker 实际完成了 4 次尝试，但 Manager 状态未正确写回，不能冒充通过。核查 PID 已退出、对应 Compose 项目无容器后，在新旧锁保护下完成 reconciliation/acknowledge；原报告不改写，也未重跑原作业。记录见 `t05-reconciliation.json`。

## T05 资源协调实施与验证

已实现共享主机预算、资源代际与消费者登记、评分积压背压、按实际镜像消费者清理、定期诊断快照 TTL/配额回收，并接入 eval、grade、build 和平台 Agent 执行入口。参见 [资源预算](resource-budgets.md)。保留旧复评槽锁，使迁移期间同用户旧复评进程仍受并发限制。

资源第一轮定向回归 **35 passed**；后续完整回归先通过 **419 passed、11 skipped**，开启真实 Docker 镜像清理后通过 **422 passed、10 skipped，72.38 秒**。实际镜像测试确认外部标签保留、仍被容器引用的镜像不删除，并断言不调用全局 build-cache prune。20/80/500 任务 fake 测试只验证有界准入和评分积压时不继续准备，不代表 500 题真实容量。

后续补齐批次构建预留、准备前磁盘估算、平台诊断/回归通道、取消状态投影与暂停心跳。最终开启 `CLEANUP_DOCKER_TESTS=1 GUARDIAN_DOCKER_TESTS=1 RSI_DOCKER_TESTS=1`，全量结果 **442 passed、3 skipped，214.82 秒**，日志 `t06c-full-tests.log`。剩余跳过项按环境条件跳过，不计入真实验收能力。

资源预算是平台各入口共享的准入账本；容器已有 CPU/内存限制继续保留。任意宿主构建和外部评分进程的预算不是操作系统级 RSS 保证。进程消失、清理失败或外部结果未知时保留待核验状态，不自动释放并重放。没有全局 Docker cache prune。

## T06 评分与结果视图验收

新增 `measurement/results.py`、`comparability.py`、`grading.py`。官方复评接入 Manager；每次实际评分执行保存不可变 GradeRun，绑定 Attempt、输入 hash、评分器与执行序号。评分重试只增加 GradeRun，不重跑 Agent；原始结果和执行日志保留。

SWE 的主结果必须有匹配当前 patch、任务记录、模型标识及报告 hash 的官方复评证据。Harbor 内置结果单独展示；一般任务的 resolved 指标不隐式当作 SWE。页面展示执行、评分、证据、资源四轴及计划/执行/评分分母、未知费用比例和不可比较原因。部分评分、缺失或重复证据不输出伪全量成功率；assisted 模式显式参与可比较判定。

回归覆盖报告/patch 篡改、缺失证据、逐次重复、模型/来源不一致、Agent 超时但官方评分完成、评分失败与正常未解出。实际 Docker RSI 验证对父版本与候选版本分别构建、执行两个 controls，父版本 0.0、候选版本 1.0；此证据只证明离线夹具闭环，不代表真实模型提升。真实容器快照/清理测试也包含在上述 442 项中。

本轮没有使用付费模型或执行新的 SWE 官方数据集实验；实际 SWE 导出/复评链路的完整产品验收仍属于 T16，不能用夹具结果替代。P2 的通用 Agent 接入、内置四种 Agent 和完整选择流程继续按路线实施。

抽样记录补充：CLI/Web 冻结题目时一并复制 `selection.json`，将其 hash 纳入 Plan，并在执行前核对。修改原始选择记录不影响已提交实验，修改冻结副本会拒绝执行。修复后的 submission/console/recipe schema 定向回归 **35 passed，1.92 秒**（`t06d-tests.log`）；其中 recipe 属于后续 T07 的早期契约验证，不计为 T07 完成。
