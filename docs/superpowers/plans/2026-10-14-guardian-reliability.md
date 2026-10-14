# 守护与 DSH 中转可靠性修复计划

**Goal:** 修复 80 题实验发现的搜索绕过中转、事件漏读、合法等待误报、守护协议失败和错误终止。

**Architecture:** 保留 Harbor 工作进程和独立监督进程。监测使用独立增量事件游标，展示仍保留有界日志尾部；守护使用原生函数工具和结构化决策；搜索走独立中转路由，保留搜索请求自己的参数。

**Tech Stack:** Python、Pydantic、Harbor、Docker、pytest；DSH 原生 Standard preset。

**Spec:** 当前对话中用户逐项认可的修复方向（2026-10-14）；原始证据 `.cache/eval-80-review/`。

## 约束

- 不覆盖历史实验，不修改现有 RSI 工作区改动，不执行付费复跑。
- 保留 15 秒轮询，默认无进展阈值改为 600 秒；原题硬期限不能被延长。
- 守护失败是平台调查失败，不能据此判定被测 Agent 卡死。
- 日志有缺口时不能沿用旧 pending 宣称工具逾期。
- 工具默认串行，多个原生调用按序执行；无调用有界纠正，保存响应与 finish_reason。
- 守护负责恢复执行，不代替被测 Agent 解题、改答案或改变评分契约。

## Task 1: DSH 搜索中转

Files: `agents/dsh.py`, `resources/live_model.py`, `resources/mock_model.py`, `tests/test_live.py`。

- [x] 审核冻结包中独立模型服务 endpoint；区分模型 API 与普通网页抓取。
- [x] 设置 `DEEPSEEK_SEARCH_BASE_URL=http://127.0.0.1:18765/search/v1`。
- [x] 新路由 `/search/v1/messages` 替换认证并转发到配置的 Messages upstream；不覆盖 search model/max_tokens/tools；日志标注 purpose。
- [x] HTTP fixture 验证请求、响应、密钥隔离和辅助调用用量记录；普通聊天归一化维持原行为。

## Task 2: 完整事件消费与长等待

Files: `guardian/probe.py`, `guardian/docker.py`, `guardian/monitor.py`, `guardian/config.py`, `tests/test_guardian_telemetry.py`。

- [x] 为监测增加独立 cursor（文件身份、字节偏移、边界校验），解析完整行并返回精简生命周期事件；普通快照不消费监测游标。
- [x] 每轮读取有界，积压继续追赶；轮转、截断、损坏、超大行明确 gap，不能静默跳过后保留旧 pending。
- [x] 同时识别 BKCrab exec 与 DSH tool/call、tool/result、job_output；合法等待期限内不产生日志静默报警。
- [x] 回归覆盖返回事件后紧跟 >64 KiB 日志、半行、重放、轮转和 600 秒合法等待。

## Task 3: 守护协议与决策

Files: `guardian/model_driver.py`, `guardian/driver.py`, `guardian/supervisor.py`, `guardian/decisions.py`, `tests/test_guardian_decisions.py`。

- [x] 系统提示交代平台背景、疑似触发、容器/宿主机边界、证据路径、期限和恢复职责；模型只使用原生函数，不输出 JSONL。
- [x] 明确结构化 decision：wait、false_positive、recovered、terminate、inconclusive。保留 finish 协议名，提供 final 别名。
- [x] 记录有界脱敏模型响应、finish_reason、用量与协议错误；零调用/无效参数最多纠正两次；多调用先校验再串行执行，terminal 只能在末尾。
- [x] 监督进程区分等待、误报、恢复验证、明确终止和调查失败；失败/不确定继续观察直至硬期限，恢复未验证重新调查而非自动杀题。
- [x] 验证 wait 与 driver failure 不触发 90 秒误杀；明确终止和硬期限仍释放资源。

## Task 4: 验收与说明

Files: `tests/test_guardian_docker.py`, `docs/experiment-guardian.md`。

- [x] 运行本地针对性测试、Linux 全量回归与 Ruff。
- [x] 将相关文件复制到服务器独立临时目录，在 Linux/Docker 环境执行离线验收，不替换线上平台。
- [x] 保留 39 个异常 Agent－题目组合为后续复跑范围；记录真实 API/付费复跑尚未执行的限制。

## 验收结果

**后续版本核对的重要更正：** 本地基线 f78d324 缺少服务器已有 b51b273 批次/异常工作区保留提交。该提交实际实现每批 20 题及每实验 3 份/20 GiB/24 小时归档，不能按本地缺文件判断线上没有此功能。本文件下述 217 项和 42 项验收均基于本地旧基线，尚未完成与服务器较新批次及 retention 模块的整合验证；不得直接覆盖线上 supervisor/worker。2026-10-14 只读核查确认 SWE80 保留机制已运行，源码副本存于 .cache/eval-80-review/deployed-retention/，需要先整合较新基线再完成部署验收。

### 用户后续修订：恢复职责与两种决策

用户明确要求简化，不再维持五类决策和恢复验证倒计时。以下修订替代 Task 3 中相应设计，先前验收记录保留为历史结果：

- [x] prompt 明确恢复只限解除运行阻塞，禁止修改被测 harness 实现；无法局部恢复时记录证据、终止本题，队列继续。
- [x] 模型仅输出 continue/terminate，其他情况在 summary 说明；旧决策只做读取兼容。
- [x] 监督进程删除恢复验证状态和倒计时，继续运行交回常规监测，保留介入证据和原题期限。
- [x] 本地验证协议与兼容行为，14 passed、5 skipped；Ruff 与 diff 检查通过。
- [x] Linux/Docker 验证继续运行、显式终止、调查失败隔离及硬期限清理：42 passed，102.25 秒（四个 guardian 测试文件）。

2026-10-14：服务器隔离目录 `/tmp/guardian-reliability-4jyYsE` 全量回归（包含 Docker）217 passed、3 skipped，114.39 秒。Ruff 与 `git diff --check` 通过。真实上游搜索和真实守护模型诊断尚未在线验收；未部署、未运行付费复跑。39 个候选组合已从原始 incident 记录提取至 `.cache/eval-80-review/rerun-candidates.json`，未改变原实验记录。
