# 平台 Agent 执行边界

公共平台执行器只有 OpenCode、DSH、Hermes。它们使用各自固定 Recipe Release 中的原生入口，平台不另写简化推理循环。用户的被测 Agent 由用户源码与配方提供，不随公共平台分发。

## 首次准备

1. 在“Agent 接入”的内置 Agent 区准备卡片指定的 Docker 镜像，构建至少一个开源 Agent；CLI 可执行 `integration build --builtin dsh`，也可选择 `opencode` 或 `hermes`。
2. 在 Linux 平台根目录运行 `python scripts/prepare-rsi-runtime.py --root "$PWD"`。它构建仅包含 Python、Git、shell 等基础工具的通用 Docker 容器，记录实际 image ID 到 `.cache/platform-runtime.json`。构建会下载基础镜像与系统依赖。
3. 在模型设置保存模型和凭据。DSH 需要 Messages 接口，OpenCode/Hermes 使用 OpenAI Chat 接口。真实接入服务需以 `--allow-live` 启动。

平台任务启动时校验并冻结所选 Release，解包后只读挂载，不把任何用户被测项目编入镜像。缺失、禁用或损坏的 Release 明确报错，不回退到其他执行器。旧的专用运行时记录不自动视为已安装。

## 接入与自行接入

“一键接入”每次独立选择执行器及模型，不复用 RSI 页面的选择。最多 50 次模型请求、30 分钟；更低的模型预算继续生效，美元费用可能未知。模型策略与角色为 integration，调用不会混入被测实验。

固定目标源码只读挂载到 `/source`，规范与 schema 位于 `/evidence`，配方及 patch 写入独立 `/workspace`，候选结果写入 `/output/integration.json`。普通 schema、构建和 conformance 检查负责决定是否准入，模型不能自行授予等级。

“自行接入”可复制平台生成的说明给外部工具，完成后提交普通配方；不需要平台执行器、平台模型或内部结果封装。外部工具的调用预算自行管理。

## RSI 与执行边界

RSI 页面单独选择执行器与模型。被测目标须有用户自己的 `rsi-profiles/<agent-id>.json`，声明源码、检查命令、标准源码配方或复评模板；平台不再推测个人仓库路径。`diagnosis` 与 `improvement` 角色分别冻结模型策略。

各次执行具有独立 HOME、输出与模型日志。原始动作和模型响应保留，超时和取消由平台控制。Guardian 保留原来的多轮 JSONL 驱动与只读调查工具边界；其模型角色为 guardian。平台不因使用同一执行器而授予完整模型/网络覆盖认证。
