# 有界批次测评

配置 `batch` 后，一个实验先冻结全部题目元数据、模型配置和 Agent 产物，再按批准备镜像。SWE Verified 的 80 题、两个 Agent 仍是一个 160 次尝试的实验；批次不改变成绩分母或外部辅助标记。

```json
{
  "concurrency": 2,
  "batch": {
    "size": 20,
    "prepare_swe": true,
    "official_regrade": true,
    "retain_count": 3,
    "retain_gib": 20,
    "retain_hours": 24,
    "min_free_gib": 20
  }
}
```

`batch` 默认关闭。开启但未指定守护时使用 observe 期限兜底；指定 assist 时继续使用冻结的守护策略。SWE 准备和复评要求 resolved 类型 benchmark；其他 Harbor 单阶段任务可以使用批次执行而关闭这两个选项。

每批按 `preparing → running（逐个 Agent、各自使用任务并发）→ regrading → cleaning → completed` 执行。先校验补丁和原始评分报告，完成本批独立 SWE harness 4.1.0 复评，再处理镜像；不会为了下批腾空间先删掉尚待复评的镜像。官方报告必须覆盖本批精确 ID 集合，评分异常或不完整报告使该批失败并停止清理。正常 unresolved 和 empty patch 保留在成绩分母中。

## 证据和空间

- `manifest.json` 固定模型/Agent/来源配置，并记录每批准备后的任务哈希、镜像 digest 和 runtime archive hash。
- `selection.json` 保存来源抽样与排除清单。任务内容、日志、补丁、守护证据与历史实验都保留。
- `shared-runtime/<sha256>.tar` 在每个冻结目录独立保存一份，各题的 archive 使用目录内硬链接；源目录变化不会改变冻结副本。任务路径和原有任务哈希定义保持兼容。
- `batch-state.json` 保存批次、阶段和运行进程身份；控制台详情显示当前批次。
- `batches/NNN/official/` 保存仅含本批 ID 的 predictions、dataset、命令日志和原始官方报告；`official-summary.json` 汇总 observed/expected 与原始报告位置。
- `batches/NNN/cleanup.json` 记录实际镜像删除返回值、容器引用保护、构建缓存清理和前后空闲字节数。

20 是任务数上限，不是固定磁盘容量保证。准备每题和进入 Agent 阶段前检查余量；不足时明确停止，不开始新的付费作答。Docker 层共享、单题镜像大小、诊断归档及其他服务仍影响峰值。清理只删除当前批记录的 SWE digest/tag 和 Harbor `hb__` 构建标签；被其他容器引用或不属于这些标签的镜像保留。清理未使用构建缓存可能使以后构建变慢，不删除生产镜像、容器、网络或数据卷。

## 异常保留

有异常、外部辅助介入或缺少关键日志/补丁/评分报告时，先停止目标容器，再把 `/testbed` 复制为 `retained-workspace.tar`。这是离线工作区文件快照；原容器、API 凭据所在的 installed-agent 目录和 container Env 不保留。已知 .env、key/token/secret/credential 文件名、凭据目录及符号链接会过滤，跳过数量与失败原因写入 `retention.json`。不承诺恢复原进程、内存、整个根文件系统或被过滤文件。

正常终止且证据齐全的题无需快照，包括 reward=0。所有原容器最终停止、精确删除以释放槽位。每个实验最多 3 份、20 GiB；归档时留 2 GiB 磁盘余量，单次快照最多 120 秒。超过配额淘汰最旧快照；24 小时期限在下一次归档时检查，不是后台定时器。超限、源工作区不可用、复制失败或清理失败均记录，不能解释为完整现场已保留。`retain_count=0` 关闭工作区归档，仍保留现有日志与异常记录。

## 恢复

```sh
agent-harness-eval run experiments/swe-live-80-assist.json --output artifacts/swe-live-80 --resume
```

正式运行应使用已冻结的配置文件恢复（当前本地实验为 `.cache/swe-live-80/config.json`）。恢复需要同一配置、完整 provenance、未修改的冻结任务及 Agent 产物；已完成批次跳过，已有终态尝试（包括失败）计入次数而不重跑。存在匹配任务的无结果尝试时拒绝自动重跑，要求先检查原进程和容器；准备阶段私有事务可按原哈希回滚。官方复评或清理失败后的恢复可再次运行独立评分/清理，不重复被测 Agent 调用。

## 验证

单元测试覆盖批次顺序、统一成绩、恢复跳过、冻结文件篡改、共享 archive 独立性、当前批复评覆盖和引用保护。真实禁网 Docker 验收覆盖工作区归档与凭据过滤；真实 Harbor 两批四次正负对照全部按预期评分。独立 SWE harness 使用 `pylint-dev__pylint-6903` 的基准补丁得到 resolved=1、errors=0；这属于离线评分连通性检查，不计为 Agent 成绩。
