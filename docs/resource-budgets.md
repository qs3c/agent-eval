# 主机资源预算与保留策略

同一操作系统用户的工作区共享 `~/.cache/agent-harness-eval/operations/resources.sqlite`。准入同时检查 CPU、内存、预计磁盘和 eval / grade / build / platform-agent 通道数量；重复申请和释放幂等，失配 owner/generation 拒绝。不同源码 checkout 不再各自认为自己拥有整台主机。

```bash
agent-harness-eval resources status
agent-harness-eval resources configure --config host-budget.json
```

预算示例：

```json
{
  "cpus": 8,
  "memory_mb": 12288,
  "disk_bytes": 85899345920,
  "lanes": {"eval": 4, "grade": 1, "build": 1, "platform-agent": 1}
}
```

仅所有预留释放后才能修改主机预算。默认值基于 CPU 数量和物理内存的 80%；部署了其他服务的机器应主动调低。此账本约束平台的准入，并非给操作系统上所有进程设置全局限额。Agent 容器继续使用实验中已有的 Docker CPU/内存限制。内置 RSI 原生容器的预留与其 2 CPU / 4 GiB 配置一致；任意外部脚本、镜像构建和官方评分器的实际峰值仍需通过运行证据校准，不能把声明预算等同于 RSS 实测上限。

进程消失不自动释放预留：可能存在遗留容器或外部调用。`resources status` 会标记 `reconciliation_required`。确认实际资源清理后，可显式释放记录中的 ID、owner 和 generation；仍存活的原进程会被拒绝：

```bash
agent-harness-eval resources release --id <id> --owner <owner> --generation <generation> --acknowledge-cleanup
```

分批实验新增 `batch.max_pending_grade_batches`，默认 2。达到失败评分积压上限时停止准备下一批，保留已完成作答和失败评分证据。先复评已有批次，再按冻结配置恢复实验；不会为了恢复评分而重新调用 Agent。原有每批题数和磁盘 headroom 检查继续生效。

SWE 准备前用 `batch.estimated_task_gib`（默认每题 2 GiB）计算本批预计占用，同时检查主机磁盘预算与实际空闲空间减去 headroom。这个值是可配置估算，不是镜像大小的保证；准备过程中和作答前仍检查实际余量。数据集镜像较大时应提高估算或降低批次题数。

镜像保留按消费者处理：准备阶段尚不知道依赖时保守阻止清理；准备完成后按实际镜像 ID 保护仍在执行或等待评分的批次，允许清理无关批次。删除前继续检查外部容器与外部标签，不使用强制镜像删除，也不再执行全局 Docker build-cache prune。

受管 Guardian 记录实际容器 ID 与 Compose 项目身份。执行和诊断消费者未释放时不能清理；删除前设置状态以拒绝新的消费者。失败清理保留错误，后台只重试已经停止、身份核验一致、无消费者的容器。活跃容器、身份不明的对象和其他项目资源不会被后台删除。

Manager 定期按冻结的保留策略清理诊断工作区快照，即使没有新实验，也会处理 TTL 与配额。只删除明确的 `retained-workspace.tar`，更新保留记录，不删除原始 result、patch、日志或评分报告。归档写入与清理共享跨进程锁。

20 / 80 / 500 任务的 fake 测试验证准入有界，不证明真实 500 题的磁盘容量或运行成本。实际验收与当前限制见 [P1 验证记录](p1-validation.md)。
