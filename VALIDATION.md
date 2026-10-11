# 本地验收记录

日期：2026-10-11。以下为已有无模型验收；真实单题试跑另行记录。

## 已完成

- `uv sync --locked --python 3.12`；Harbor 0.24.0。
- `pytest -q`：23 项通过。覆盖不合法配置、能力要求、凭据字段拒绝、live 显式费用策略、流式 relay 与凭据不落日志、独立第三方适配器、失败/缺失/损坏结果统计、两种协议和 ATIF、DSH 必需模块审计、完整 SWE 补丁与 attempt 分离导出。
- `go test -race ./cmd/bkcrab-eval`：通过；验证同一任务目录的文件/shell 映射、证据脱敏、stream 错误传播。
- `ruff check src tests scripts/*.py`、`git diff --check`。

| 运行 | 正对照 / BKCrab | 负对照 / DSH Standard | 证据含义 |
|---|---|---|---|
| `artifacts/controls-final` | 两任务 reward 均为 1 | 两任务 reward 均为 0 | verifier 正负对照 |
| `artifacts/native-final` | 两任务 reward 均为 1 | 两任务 reward 均为 1 | 模拟模型驱动原生 loop；不是能力分数 |

两个 native Agent 共 4 个 trial，无运行异常，无缺失。每个 trial 均有原始事件、wire requests/responses、ATIF、coverage。DSH runtime audit 显示 `standard` 的 26 个活跃模块，`leakedServices=[]`，stderr 无缺失服务警告。短测试仅触发文件工具，不能代表压缩、子代理等全部行为都已测试。

## SWE

任务：`django__django-13741`，SWE-bench Verified。

- HF 数据读取时的 revision：`c104f840cc67f8b6eec6f759ebc8b2693d585d4a`；本地固化该任务完整数据记录供复评。
- Harbor generator：`b53b8134e1241686dca7759af188f987ecc48e8b`。
- Docker：`swebench/sweb.eval.x86_64.django_1776_django-13741@sha256:c4c76e39029d47b01bdbcd2b78077c736467b616dc64c01ac62f6793068b7f27`。
- 从 Harbor oracle 工作区、隐藏测试开始前提取的补丁，已在 **官方 SWE harness 4.1.0** 的独立干净容器复评：`resolved=true`，1 submitted / 1 completed / 1 resolved / 0 errors。
- 官方空补丁对照：1 submitted / 1 empty patch / 0 resolved。官方对空补丁跳过执行，因此不将它描述为“官方测试已跑但失败”。
- 官方证据：`artifacts/official-regrade/oracle.json`、`oracle-logs/`、`nop.json`；输入：`artifacts/swe-oracle-captured.jsonl`、`swe-empty.jsonl`。
- Harbor 最终正负对照 `artifacts/swe-validated`：oracle reward=1，nop reward=0，均无运行异常。oracle 83 项测试通过；nop 命中 1 项预期失败。可导出的 predictions 位于 `artifacts/swe-predictions/`。

前两次 Harbor SWE 调试运行在 verifier 临时下载依赖期间主动取消，保留 `artifacts/swe-controls-01` / `02` 供诊断，不能当成模型失败。调试运行 `artifacts/swe-controls-final` 进一步暴露上游 parser 评分时访问 GitHub 的问题，其零分不是有效 Agent 结果。最终版本在准备阶段构建并固化 TestSpec、打包评分运行时；保留上游 parser 4.0.3，约束 pyarrow 20.0.0，固定实际包清单并保存任务文件哈希。

两个原生 Agent 还各自通过同一 SWE 任务的模拟模型执行链路（`artifacts/swe-native-mock`）：两边运行正常、补丁成功导出、reward 均为 0，符合模拟模型只写测试文件而不修复问题的预期。该实验验证 Agent/benchmark 组合接入，不能作为模型能力对比。补丁位于 `artifacts/swe-native-predictions/`。

最终导出的 oracle 补丁与已完成官方复评的输入逐字相同。另将 BKCrab 模拟运行导出的非空补丁送入官方 harness 4.1.0：补丁成功应用、测试执行完成、resolved=false、0 errors，符合预期，见 `artifacts/official-regrade/mock-negative.json`。报告按 task_id 的目录身份配对，兼容 SWE 的展示名称和目录名不同；回归测试已覆盖这一情况。

## 真实评测状态

用户已授权官方 DeepSeek V4.1 Flash、单题、两边各一次、不设费用上限。已验证官方 `/models` 返回 `deepseek-flash`，接入 relay 并开始 `artifacts/swe-live-one`；两边均已通过官方独立复评；初次 BKCrab 因意外技能学习后台调用标为诊断，隔离开关修正后同题重跑也通过。最终结果见 `LIVE_VALIDATION.md`。

当前 ATIF 是末次请求上下文加终态，完整调试请看原始日志；usage 不存在时为 null。模拟 tokens 是传输测试计数。BKCrab 来源包含用户现有 dirty checkout；保存了源码内容哈希与实际二进制，异机源码重建仍需保存原 checkout 的既有变更。

仅新增 BKCrab 的 `cmd/bkcrab-eval`，未修改其既有工作。没有推送、部署或创建远程 PR。
