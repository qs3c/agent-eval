# 本地工作区兼容与迁移

P0 建立旧格式夹具和只读历史登记回归测试。P1/T03 已新增 `.workspace/state.sqlite`、领域对象、事务事件、schema 备份和显式导入命令。**导入只建立索引，不切换执行调度、不恢复或重跑旧作业**；现有 `.console/`、RSI、复评队列和 artifacts 继续工作。统一 Manager 属于后续 T04。

## T03 已实现的导入入口

```bash
# 默认 dry-run：校验来源、列出身份、schema 变更和索引 payload 字节数，不创建新状态目录。
uv run agent-harness-eval workspace-import --root /absolute/path/to/workspace

# 所有该工作区写入方停止后显式导入；来源原文件和旧 SQLite 保持不变。
uv run agent-harness-eval workspace-import --root /absolute/path/to/workspace --apply

# 用户级复评库必须显式指定，只导入 output 属于此工作区的记录。
uv run agent-harness-eval workspace-import --root /absolute/path/to/workspace \
  --regrading-root /home/USER/.cache/agent-harness-eval/regrading --apply
```

`index_payload_bytes` 只是序列化索引数据量，不是 SQLite、备份或全部 artifacts 的磁盘需求估计。首次申请时生成 schema v1；升级前 SQLite backup API 创建独立备份，DDL 或导入事务失败会 rollback。备份位于 `.workspace/backups/`，不替换正在打开的数据库文件。代码拒绝读取更高版本 schema 为当前版本，也拒绝符号链接指向的新状态库。

导入使用旧控制台 `server.lock`、各实验 `.run.lock`、RSI candidate `operation.lock`、新 Manager 锁，以及显式复评目录的 `batches.lock`，任一持锁则拒绝。旧队列仍标为 running 时要求先核对状态；遗留 WAL 需停止写入方后先按 SQLite 的 checkpoint 流程处理，避免忽略 WAL 内容。锁检查不是停止服务命令，也不授权迁移期间继续使用旧写入入口。

旧 manifest 映射为不可变 `ExperimentPlan`，task hash 缺失就保留缺失，Agent source/recipe 未知就保持 unknown；不会生成虚构的 Release 来源。索引同时保存原始路径与文件 SHA-256；没有复制大型日志。运行、RSI 和复评队列记录保存为 legacy 快照，不成为新 jobs。

重复导入相同身份与内容时新增数为 0；同身份内容改变时整次导入失败，不用新状态覆盖旧快照。快照导入不是持续同步服务。实际 schema migration 成功/失败及导入计数均可单独查看；原始 evidence 的任何修改仍需由原有完整性检查处理。

内部接口：`workspace.Store.create_plan(plan, idempotency_key)` 对相同请求幂等，对相同 key 不同输入报冲突；`pending_events()` 和 `acknowledge(seq)` 提供至少一次交付的事务 outbox，消费者须幂等，不能声称外部 exactly-once。Attempt/GradeRun/Incident 的定义与四轴状态分离，generation 防止过期写入；原始 Plan 读回时重新核对 digest。

canonical JSON 仅对声明为集合的 capabilities/requires 及实际集合排序，任务和 Agent 列表顺序仍影响身份。拒绝已知凭据字段和非有限数字，允许 `_env`/`_ref` 引用；不扫描日志，也不保证识别任意文本中误粘贴的 secret。

## 现有入口与状态

| 类别 | 当前入口 / 文件 |
| --- | --- |
| 实验 CLI | `validate`、`run [--resume] [--workbench-repair]`、`report`、`serve` |
| 来源与评分 CLI | `snapshot-bkcrab`、`bundle-dsh`、`manifest-index`、`manifest-verify`、`swe-export`、`swe-instrument`、`swe-regrade` |
| RSI CLI | `rsi-case`、`rsi-candidate`、`rsi-action`、`rsi-work` |
| 新增 CLI | P0 `doctor`，不写状态；T03 `workspace-import`，默认预览、`--apply` 写新索引 |
| Web 实验 API | `/api/templates`、`/api/runs`、`/api/runs/{run_id}`、logs/comparison/incidents/traces 子路由、`/api/compare` |
| 复评 API | `/api/runs/{run_id}/batches/{number}/regrade` 与 `regrade-log` |
| 模型与 RSI API | `model_pool.py` 与 `rsi/api.py` 注册的路由；保持现有契约，不在 P0 调整 |
| 控制台索引 | `.console/runs.sqlite3`：`runs` 表；另有 inputs/logs、模型配置及凭据文件，备份整个目录 |
| RSI 状态 | `.rsi/queue.sqlite`：`actions` 表；`.rsi/{cases,candidates,actions}/<id>/record.json` 与证据 |
| 用户级复评 | `~/.cache/agent-harness-eval/regrading/queue.sqlite3`：`jobs` 表及 active_batch 索引；同目录锁和镜像租约 |
| 实验事实 | `artifacts/<run>/manifest.json`、配置/来源清单、冻结 tasks、jobs/*/*/result.json、原始日志、report、batch-state、official-summary、batches/*/official/ |

现有 `Console.import_history()` 将 artifacts 登记到控制台索引，重复调用使用稳定路径身份和 `INSERT OR IGNORE`；它不是通用跨版本迁移。历史详情状态可从结果重新计算，所以 SQLite 的初始 `incomplete` 不能当作最终评分。

## 夹具约定

`tests/fixtures/legacy_workspace/` 保存脱敏控制实验和从它派生的状态：

- completed：保留正负对照结果，成本未知仍为 null；不包含用户源码、key 或原始 prompt。
- interrupted：模拟未完成的旧批次，保留状态而不伪造已完成结果。
- partial-grade：正常执行记录与部分官方评分形状并存，明确是状态测试，不声称 controls 真正调用过 SWE scorer。
- `.console/runs.sql`、`.rsi/queue.sql`、`host-regrading/queue.sql` 是旧 schema/记录的文本快照；测试只在临时目录还原 SQLite，不提交带本机路径的二进制数据库。
- `hashes.json` 覆盖除自身及说明文件外的夹具原始文件。fixture 强制 LF，重复读取/登记前后核对字节 hash；原始产物只读，允许新索引在目标工作区写入。

测试使用实际 Console 历史登记和 RSI/复评读接口，不创建一个“只在测试里实现的新迁移器”。缺失 task hash/provenance 保留缺失，UI/比较拒绝据此认定条件一致；没有补造 source SHA 或可比较认证。

这些夹具不覆盖所有生产历史版本；未来新增 migration 必须对它们及其新增真实格式样本同时验收。不得为了让旧 fixture 通过而修改原始输入或向历史实验回填虚构来源。

## 冻结与恢复规则

`run.resume_configuration()` 只规范化 Agent capabilities 和 benchmark requires 的集合顺序；任务列表、Agent 列表、资源、模型和其他条件的顺序/值保持不变。集合序列差异不应误报不一致，交换任务/Agent 次序仍应被发现。

`verify_workbench_repair()` 当前允许范围是 `batching.py`、`benchmarks/swe.py`、`run.py`、`cli.py`：必须 schema_version=1、非空 reason、完整前后 inventory 严格一致、文件键集合相同，改变的文件是允许集子集。新增文件、改 Agent 行为、缺少/伪造 hash 或无审批记录均拒绝。

这里的函数只校验清单，不替用户判断修复是否真的属于基础设施；完整恢复流程还校验 frozen tasks/config 并在 `recovery/` 保存修复证据。原始 manifest 不能重写。P0 增加 doctor 是新功能，不应把这个版本作为绕过已有实验冻结清单的理由。

## 迁移操作与后续调度切换门槛

1. 停止所有使用该 workspace 的 Web/CLI/RSI 写入方；协调同用户其他 checkout 的共享复评队列。新旧调度 owner 不得同时运行。
2. 复制静止 SQLite、所有必要证据和配置到独立备份；记录版本及 hash 清单，验证备份可读。仅复制 artifacts 不足以恢复队列。
3. dry-run 显示旧路径、记录数、缺失来源、预计索引空间和不支持格式；原始实验仍只读。
4. 使用 schema transaction 与幂等旧身份映射导入；已存在同身份但内容不同报冲突，不覆盖。
5. 失败恢复匹配版本的数据库备份，并保留失败日志；不让旧程序写入新版 schema。新格式实验不能保证能在旧版恢复运行。
6. 再次导入不增加相同记录；故障注入后旧 evidence hash 完全不变。通过后才能切换入口，不能先删旧目录。
