# SWE-20 临时后台看护

`watch-swe-live-20.py` 仅看护现有 `artifacts/swe-live-20` 实验，每 30 秒检查一次。它不是 BKCrab 的根本修复，也不改变冻结的 Agent 二进制、模型配置、单题时限或原始评分。

自动恢复必须同时满足：

- 容器名称、Compose project/service 标签、任务配置路径均匹配本实验尚未结束的 BKCrab trial。
- 事件日志仅有一个尚未返回的前台 `exec`；调用时间超过显式 timeout（默认 120 秒）另加 45 秒缓冲。后台调用、sandbox 调用或多调用并行时不干预。
- BKCrab 存活，但直接子进程 shell 已不存在。
- 被选进程是调用期间创建、父 PID 为 1 的残留进程，并通过管道与 BKCrab 的输出捕获连接。

先保存证据和辅助运行标记，再发送 SIGTERM；3 秒后仅对同一身份仍存活的残留进程发送 SIGKILL。发送前重新验证 PID、进程启动时间及父 PID，并用 Linux pidfd 防止 PID 重用。镜像缺失 Python pidfd 接口时使用 Linux x86_64 syscall；无法安全清理则记录错误，不退化为宽泛 kill。

## 运行和停止

后台服务：`swe-live-20-watchdog.service`（用户级 transient systemd 服务）。`Restart=on-failure`；单例文件锁防止重复启动。正常完成或流水线消失时退出。不自动重跑题目。

```bash
systemctl --user status swe-live-20-watchdog.service
systemctl --user stop swe-live-20-watchdog.service
```

服务通过 `systemd-inhibit` 在运行期间阻止系统普通休眠，退出后自动释放；不能防止关机、断电或强制休眠。

只读检查（后台服务运行时单例锁会拒绝第二个实例，应直接查看状态文件）：

```bash
python3 scripts/watch-swe-live-20.py --once
```

## 记录和评分口径

- `artifacts/swe-live-20/watchdog/status.json`：最近检查时间、实验状态、异常及检查结果。
- `logs/swe-live-20-watchdog.log`：连续运行日志。
- `artifacts/swe-live-20/interventions/*-watchdog/`：每次干预前后快照及信号结果。
- 任务目录 `human-intervention.json`、`watchdog-interventions.jsonl`：人工授权的自动干预标记和记录；不覆盖之前人工记录。
- `artifacts/swe-live-20/HUMAN_INTERVENTIONS.md`：总索引。

**原始 Harbor / Web 汇总不会自动排除干预任务。** 复盘时必须按上述标记单列辅助运行，不把它们当作纯自主成功。中途加入看护本身也需在实验方法中披露。

本脚本不会根据 CPU 高或长时间没有模型请求就杀任务，不处理模型 API 错误或网络故障，也不操作 DSH 和官方评分容器。单题总时限仍由 Harbor 处理。本聊天另有每 10 分钟心跳检查，仅在新干预、持续故障或完成时通知，结束后停用。

## 验证

```bash
.venv/bin/python -m pytest tests/test_swe_watchdog.py -q
SWE_WATCHDOG_DOCKER_TEST=1 .venv/bin/python -m pytest tests/test_swe_watchdog.py -q
```

第二条需要本机已有 `swebench/sweb.eval.x86_64.django_1776_django-9296:latest` 镜像；只创建临时、禁网、只读根文件系统的测试容器，不调用模型。复现 shell 被杀、管道子进程残留，验证阻塞解除、无关进程存活和标记落盘，最后删除测试容器。
