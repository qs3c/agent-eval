# 本机 Manager 与实验控制

Manager 是当前本地工作区的执行所有者。Web 是命令客户端，关闭页面或停止 Web 不取消实验。状态和事件保存在 `.workspace/state.sqlite`；原有 `.console`、RSI 与复评表暂时作为兼容收件箱及读视图保留。Manager 同时持有新锁和旧控制台锁，避免迁移时存在两个消费者。

```bash
agent-harness-eval manager start --root .
agent-harness-eval manager status --root .
agent-harness-eval operation list --root .
agent-harness-eval operation pause <operation-id> --root .
agent-harness-eval operation resume <operation-id> --root .
agent-harness-eval operation cancel <operation-id> --root .
agent-harness-eval manager stop --root .
```

`run` 默认提交冻结配置到 Manager，并等待结果；标准输出给出 operation ID。CLI 关闭不自动取消已提交作业。Web 启动会连接或启动同一个 Manager。`rsi-action --execute` 与独立 `rsi-work` 也经过相同调度入口。实际 Worker 使用继承的所有权与代际信息进入执行器，不能再次入队。

Manager 的 `--allow-live` 是进程级策略。已运行 Manager 的策略与调用方不同时拒绝连接；更改策略需先显式停止 Manager，再按所需策略启动。这里不会保存 API key 明文，也不会因为旧队列含有 live 配置就绕过当前策略。

`manager stop` 排空当前作业后退出，尚未开始的作业留在队列；暂停的当前作业需要恢复才能排空。`manager stop --cancel` 取消待执行作业并通知当前 Worker 清理后退出。取消不会删除已落盘证据。Manager 日志在 `.workspace/manager.log`，CLI 实验日志在 `.workspace/logs/<operation-id>.log`，Web 实验和 RSI 继续使用原日志目录。

暂停在 attempt 准入边界生效：`pausing` 表示已阻止新尝试且尚有在途尝试，`paused` 表示执行器已到达静止边界。恢复同一进程的暂停不增加 generation，也不重新执行已完成尝试。RSI 与独立复评尚不提供内部检查点，活动期间暂停会明确拒绝；尚在队列时可暂停。

崩溃恢复不等于自动重试。Manager 启动核查 PID 和 Linux 进程启动时间，旧 Worker 回包必须匹配 owner/generation。无论遗留进程仍存活，还是外部请求结果未知，都保留 `interrupted` 并阻止继续调度，不能用 `resume` 重放。确认进程、容器和外部动作结果后，可在本机 CLI 确认已检查：

```bash
agent-harness-eval operation acknowledge <operation-id> --acknowledge-cleanup --root .
```

确认只解除后续队列阻塞，不把中断作业改成成功，也不重新执行。仍存活的原 Worker 不能确认。原有批次 `run --resume` 继续校验 manifest、配置、已完成结果和代码清单；有缺失终态的尝试仍须检查，不能仅凭进程退出推断请求没有执行。

当前是 P1 实施中的接口说明，完整验收状态以 [P1 验证记录](p1-validation.md) 为准。资源总预算、四轴结果展示和通用 Release 接入分别由后续任务交付。
