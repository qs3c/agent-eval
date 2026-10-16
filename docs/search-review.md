# SWE 搜索请求审查

本版审查平台 `/search/v1/messages`（DSH 冻结搜索插件）及 `/search/exa`（BKCrab Exa platform-relay）的搜索请求。JEV 接收当前尝试的完整题目和搜索词，判断是否直接查找该题现成解答。允许时原样转发搜索请求；不审查返回结果，不拦截 web_fetch、shell 或其他直连网络。BKCrab 仍需显式启用 Exa platform-relay；direct、Brave、SearxNG 不在审查范围内。

## 配置

在实验 JSON 的 `model` 中添加以下配置；未配置或为 null 时不启用。以下为 OpenRouter 固定版本配置，密钥必须为 OpenRouter API key：

```json
{
  "search_review": {
    "endpoint": "https://openrouter.ai/api/v1/systemone",
    "model": "typesafe/jev-1.13-20260917",
    "api_key_env": "EVAL_SEARCH_REVIEW_API_KEY",
    "timeout_seconds": 10,
    "deny_threshold": 0.5
  }
}
```

执行平台进程须能读取该密钥环境变量；不要将密钥填入实验 JSON。审查仅支持 live 模式，沿用实验已有 live/spending 授权要求。参数随实验清单冻结。当前没有新增页面开关或模型池场景；直接在实验配置中指定。

OpenRouter 会将短模型别名解析成实际版本；本实现要求请求与响应模型名完全一致，因此使用完整日期版本，不使用 `jev-latest` 或 `typesafe/jev-1.13`。参考 [OpenRouter System One 文档](https://openrouter.ai/docs/guides/community/typesafe-sdk)。SWE 批次准备自动将审查域名加入 Agent 白名单；非批次任务须预先在 `task.toml` 中配置该域名，加载时检查。服务器密钥使用 `~/.config/agent-harness-eval/search-review.env`（0600），由评测服务的 systemd drop-in 加载；历史冻结实验不会自动启用审查。

协议与服务器现有 JEV 客户端一致：`POST endpoint`，Bearer 认证，发送 `model`、`state: {task, search_query}`、`questions.direct_answer`（`type: noul`）。响应须含对应 model、`answers.direct_answer: {type: noul, noul: 概率}` 和非负整数 `usage.input_tokens/output_tokens`。概率达到阈值判为“是”。0.5 是显式默认决策阈值，尚未通过真实 JEV 数据校准；不是模型输出的天然布尔值。

## 反馈与记录

- `403 / EVAL_SEARCH_DENIED`：判断为直接搜答案，不执行搜索，提示改查通用原理或 API 文档。
- `503 / EVAL_SEARCH_REVIEW_UNAVAILABLE`：超时、服务错误、无题目或无效响应；不执行搜索，但不标记违规。原始错误正文不转交给 Agent。
- `400 / EVAL_SEARCH_INVALID_REQUEST`：不符合冻结 DSH 搜索请求格式，不执行搜索，也不绕过审查。

DSH 插件会将 HTTP 错误中的 `error.message` 放入工具错误，另附自身的端点检查提示。平台拒绝模板明确说明这是策略限制、无需修改端点或凭据。一次多查询工具调用会产生多个搜索请求，每个请求分别审查。

`search_review.jsonl` 记录请求编号、策略版本、JEV 模型、阈值、判断、概率、耗时及有效响应的用量/可选费用；不保存 JEV 原始正文或凭据。审查用量独立于 Agent 模型 token 汇总，失败调用的真实用量可能未知；审查耗时仍占当前工具/实验墙钟时限，本版没有延长时限。请求日志沿用原有采集机制，本版没有实现日志隔离或强制网络出口，不应描述为全平台防绕过机制。

## 验证边界

自动测试使用模拟 JEV 和搜索上游，验证两条路径的 HTTP 拦截、原样放行、拒绝反馈、故障区别、严格响应验证、配置及密钥传递。Exa 拒绝响应保留在独立 `search_responses.jsonl`，不混入模型响应统计。2026-10-16 已从服务器验证真实 OpenRouter 固定版本调用成功；少量连通性样例不构成语义分类准确率评测，历史实验配置保持原样。

2026-10-15：相关测试 45 passed，全量回归 305 passed、9 skipped。实际实验冻结 DSH 插件的额外兼容性测试 1 passed：确认插件异常保留 HTTP 403 与 EVAL_SEARCH_DENIED，没有访问搜索上游。这验证了插件的错误传递，不代表验证了受测模型接下来是否遵循提示。

复现插件兼容性检查：将冻结 runtime.tar.gz 解压到临时目录，在 Linux 项目环境执行 `DSH_SEARCH_RUNTIME=/path/to/runtime PYTHONPATH=src python -m pytest tests/test_search_review.py::test_frozen_dsh_provider_preserves_platform_denial -q`。需要 Node.js；测试只访问本机模拟服务，不调用真实 JEV 或 DeepSeek。

2026-10-16 整合远端 Exa relay 后，隔离 Linux 目录全量回归 `325 passed、10 skipped`；冻结 DSH 插件额外测试 `1 passed`。现有 BKCrab eval 二进制配合模拟模型及拒绝判断，观察到一次搜索、两次模型请求，`EVAL_SEARCH_DENIED` 进入后续模型输入，未访问搜索上游；证据位于服务器 `/tmp/jev-bkcrab-denial-20261016/validation.json`。真实 OpenRouter 使用本模块分别判断通用文档查询（0.07，允许）和直接索取补丁（0.98，拒绝）。这些检查不代表真实 Agent 遵循提示或阈值经过校准。
