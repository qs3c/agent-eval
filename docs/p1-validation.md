# P1 实施与验证记录

日期：2026-10-26。当前完成 **T03：领域对象与本地状态库**；T04 统一管理进程、T05 资源协调、T06 统一评分视图仍未完成。本文不将 T03 视为整个 P1 已发布。

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

导入是显式、一致性快照，不是热迁移或持续同步。用户需停止旧写入方；锁和 running/WAL 检查是防误操作保护，不会主动停服务。导入不启动实验、不重试 RSI、不重新评分。

代码使用 SQLite rollback 恢复失败事务，而不是盲目覆盖打开的 DB 文件；独立备份用于离线恢复。目录内可能留下新 schema 数据库、备份和 owner 锁文件，但不会留下失败导入的半批记录。不是删除整个 `.workspace/` 才算“回滚”。

新领域对象和 jobs 表只是 T04 的基础，当前 Web、CLI、RSI 仍使用既有执行生命周期。暂停/取消、Worker fencing、自动恢复、全机容量准入和官方评分统一视图必须经过对应后续任务验收后才能宣称可用。

本次保留 `resources/live_model.py`、`tests/test_relay_roles.py`、`docs/interview/` 原有未提交内容；实施前后 SHA-256 与 [P0 保护清单](p0-validation.md#原有未提交改动保护)一致，不加入本次提交。
