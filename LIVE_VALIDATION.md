# DeepSeek V4.1 Flash 单题真实试跑

2026-10-11，SWE-bench Verified `django__django-13741`。BKCrab 与 **DSH Standard 均通过官方 SWE harness 4.1.0 的独立复评**，各 1 completed / 1 resolved / 0 errors。仅涉及这一题，没有扩展到其他 SWE 问题。

## 最终采用的运行

| Agent | 官方 resolved | Agent 执行时间 | 模型请求 | 总输入 tokens | 输出 tokens |
|---|---|---:|---:|---:|---:|
| BKCrab（修正隔离开关后） | true | 65.10 秒 | 23 | 461,018 | 9,257 |
| DSH Standard | true | 86.52 秒 | 36 | 755,504 | 10,591 |

时间仅覆盖原生 Agent 执行，不含环境准备与 verifier；它包含 Agent 自行运行测试的时间。输入量是各请求累计输入，**包含缓存命中**，不是不同文本的去重长度。两份有效运行的请求 ID 与响应 ID 完整匹配，响应均为 HTTP 200，usage 均存在。真实账单金额未查询或估算，报告中为 null；不能将 tokens 直接解释为实际付费金额。

本轮只是接通真实链路的一题 smoke。单次运行有随机性和共享主机噪声，不能据此判断整体能力、速度或成本优劣。

## 一致条件与 Standard 核验

- 官方模型 ID `deepseek-flash`，对应 [DeepSeek V4.1 Flash](https://api-docs.deepseek.com/quick_start/pricing/)，已通过 `/models` 验证。
- 模型输出上限 32,768；上下文 1,000,000；thinking enabled，effort high，temperature 1，top_p 0.95。relay 分别映射 OpenAI / Messages 参数，保留归一化前后请求。
- 同一任务文件哈希、同一 Docker digest、每边 2 CPU / 4 GiB、Agent 超时 1,800 秒。运行串行，使用独立工作区和 home；用户明确授权费用不设上限。
- DSH 固定 `0.2.1-alpha.2`，通过官方 preset registry 加载 Standard；运行时审计为 26 个模块、`leakedServices=[]`。SDK 是无界面启动载体，未使用 sdk-minimal。
- BKCrab 保留原生 Manager / HandleMessage / 工具，采用隔离 coding profile，显式关闭自动技能学习。两边系统提示、工具和 Agent 内部策略仍各自保留。
- 两个最终运行使用完全相同的 relay 源码 SHA：`fb893ff775b9fd56a5f465495d1d6582bec6afa2563533025fc19bad349c3a92`，各自日志目录也保存实际 relay 源码。

## 诊断运行与修正

首次 BKCrab 已生成通过官方复评的补丁，但完成任务后额外触发了技能学习后台请求：28 次请求、27 次有完整响应。原因是生产配置 `SkillsLearnerCfg.Enabled=nil` 的默认值为启用，评测入口最初未显式传 false。其原始 Harbor 结果中的 token 数字只覆盖已完成响应，不能当成全部用量。

修正仅发生在 `cmd/bkcrab-eval` 的 Manager 初始化，通过 `WithSkillsLearner(...Enabled: &false)` 显式关闭后台学习；未修改 BKCrab 生产默认值。随后仅重跑同一道题的 BKCrab。最终采用该修正运行和首次有效的 DSH Standard 运行；不是按分数择优。初次 BKCrab 作为诊断保留，费用总额也可能包含被中断的后台请求。

另修正了轨迹汇总：请求/响应数量不匹配时，总 usage 标为未知。新增了回归测试。原始诊断产物不重写，限制说明见 `artifacts/swe-live-one/DIAGNOSTIC-NOTE.md`。

## 证据位置

- [比较数据](artifacts/swe-live-official/comparison.json)
- [BKCrab 最终运行](artifacts/swe-live-bkcrab-corrected/report.json)；[官方复评](artifacts/swe-live-official/bkcrab-corrected.json)；[输入补丁](artifacts/swe-live-bkcrab-corrected-predictions/bkcrab.attempt-1.jsonl)
- [DSH 所在运行](artifacts/swe-live-one/report.json)（其中 BKCrab 条目为诊断）；[DSH 官方复评](artifacts/swe-live-official/dsh-standard.json)；[输入补丁](artifacts/swe-live-predictions/dsh-standard.attempt-1.jsonl)
- 官方测试日志：`artifacts/swe-live-official/live-one-bkcrab-corrected/` 与 `live-one-dsh/`。
- 各运行 `jobs/<agent>/<trial>/agent/` 保留原生 events、请求、流式响应、用量、ATIF、配置与来源；`verifier/` 保存捕获补丁与隔离评分。

补丁在隐藏测试应用前捕获；官方复评重新创建干净容器、应用导出补丁并执行测试，两边均无评分错误。首次诊断 BKCrab 的官方报告另存为 `bkcrab-diagnostic.json`。

## 接入验证

- Python：23 tests passed；ruff 和 diff whitespace 检查通过。
- Go：`go test -race ./cmd/bkcrab-eval` 通过。
- 改造后的模拟传输回归：两个 Agent × 两个非 SWE 文件任务全部通过，见 `artifacts/native-relay-regression/report.json`。
- 已扫描 115 个源码/配置/运行证据文件，未发现 API 密钥。持久密钥文件权限 0600，Git 忽略；真实密钥不写入 manifest 或认证日志。

实际 API 调用现已停止。后续启动需显式运行新的实验命令；不会自动扩大题量。
