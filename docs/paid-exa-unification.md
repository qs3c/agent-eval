# 四 Agent 的 Exa 账号统一

用户要求暂停后续调度、保留已完成题目，统一搜索服务后继续。四 Agent 共享服务器注入的 `EXA_API_KEY`；配置和命令日志中只保留环境变量名或容器内私有文件路径。

| Agent | 搜索接入 | 网页读取 |
|---|---|---|
| BKCrab | 原生 Exa 请求，经平台 REST 中转 | 原生 fetch |
| DSH Standard | 原生搜索请求经平台转为 Exa REST，结果适配回原生结构 | 原生 fetch |
| OpenCode | 原生 Exa MCP，使用同一 API Key | 原生 webfetch |
| Hermes 本地修复版 | 原生 Exa SDK，显式 paid tier | 原生 Exa SDK extract |

统一服务商、账号与付费额度，不替换 Agent 循环，不强求原生工具的参数、结果数、摘要长度或网页提取机制完全相同。DSH 的搜索来源已由 DeepSeek 辅助模型变为 Exa，这是明确记录的实验条件变化。

Hermes 的 `exa-py==2.10.2` 来自上游锁文件，在冻结前安装；运行时禁止懒安装。新包保留原修复版的所有已有文件字节，只增加 45 个 SDK 文件；旧包保留。`web.provider_tier.exa=paid` 强制付费模式，并显式关闭 `web.keyless_fallback`、`web.keyless_rescue`，禁止失败后转入匿名后端。

## 当前实验的分段

实验 `b9642f358f4440959bbf1842d5e2dc5e` 在首批四 Agent 各 3 题完成后暂停，共 12/120 次。后续 108 次使用统一账号。前 12 次不重跑、不替换；不能将全量 120 次声称为从头到尾完全一致的搜索条件。应分别报告首批和切换后的 27 题配对结果。

原始清单、配置、已完成文件哈希、上游检查、付费渠道实测和迁移记录位于该实验的 `recovery/paid-exa-unification/`。只有实测通过后才迁移并恢复；官方复评继续关闭。

## 上游核实

2026-10-18 拉取的 Hermes main 为 `ad06e63c9eeb4a60806041a843f7cf698b835fd2`，最新稳定版 `v0.22.2`（`d923a5eb`）也存在同样逻辑并已复现。两者仍保留短字符串包含 `"error"` 即判失败的兜底。直接执行该函数 AST（只替代无关显示及拒绝检查依赖）可重现 `error:null` 被判失败。实验原冻结版是 `06671b7`，不是此次 main；正式继续使用明确标注的本地修复版 `468d8dff`。

DDGS 是元搜索 Python 库。冻结的 Hermes DDGS provider 只接搜索；不能据此声称最新版 DDGS 库没有提取能力。Exa 官方 MCP 提供限流的匿名免费额度，MCP 协议本身不决定收费；认证后使用账号额度和套餐。

## 验证证据

- Python 全量回归：440 passed、12 skipped；最终付费接入相关回归：23 passed。
- 四原生 Agent 实测：`58368bcc4da94a30954cd0f787caeee0`，均完成真实搜索与网页读取。
- Hermes 最终禁止匿名回退后的实测：`4e12ac58fa344a6d905a7b4966d0a88b`，两次相同网页读取和一次搜索成功。
- 验收核对真实工具返回内容，不以烟测 verifier 的文件检查代替 Web 验证。
- MCP 原生查询参数认证对照：无效 Key 的搜索被拒，配置 Key 的同一搜索成功，确认没有忽略账号认证。
- 全部实测结束后扫描原始 Agent 日志，未检出真实 Exa Key。
