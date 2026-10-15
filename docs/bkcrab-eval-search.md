# BKCrab eval 原生搜索

eval 入口使用 BKCrab 原生 `RegisterWebSearchChain` 和现有 Exa、Brave、SearxNG provider，不依赖正式网关、数据库或用户会话。默认关闭；显式配置后注册 `web_search`。搜索能力不等于浏览器自动化，也不自动开放 `web_fetch` 访问所有网站。

在 BKCrab Agent 的 `kwargs` 中添加：

```json
"web_search": {
  "provider": "exa",
  "transport": "platform-relay",
  "credential_env": "EXA_API_KEY"
}
```

Brave 使用 `"provider": "brave"`。两者的凭据由启动平台/CLI 的环境提供，不能写入实验 JSON。适配器通过 `/installed-agent/search.key` 私有文件传入 eval；配置、命令和证据中不包含密钥。缺少指定凭据会在实验准备前报错。

## Exa 密钥与请求

按官方 [build-with-exa](https://github.com/exa-labs/agent-skills) 技能接入：`POST https://api.exa.ai/search`，使用 `type: auto` 与 `contents: {highlights: true}`，从结果 `highlights` 数组读取相关片段。未指定 count 时采用服务默认数量；不附加分类、域名或日期过滤。

在本机终端执行以下命令，隐藏输入密钥：

```sh
python3 /home/xavier/Desktop/github/agent-harness-eval/scripts/setup-exa-key.py
```

已有只包含密钥的文件时，可使用 `--from-file /absolute/path/to/key`。脚本将环境变量写入 `~/.config/agent-harness-eval/exa.env`，文件权限为 0600，不打印密钥。本机 console 的 systemd drop-in 已配置读取该文件；保存密钥后，需要重启服务才能载入。此设置不会自动修改当前 Codex 进程环境或正式 BKCrab 数据库配置。

Exa 修复后的构建与来源快照位于 `.cache/bkcrab-exa-20261015/`。这仍是基于 `1fbd96f` 加本地修改的构建，并非已提交发布版本。公开网络 Exa 验证须等实际密钥配置后执行。

SearxNG 不需要 API key，使用 `{"provider":"searxng","endpoint":"https://your-search-service.example"}`。地址必须是无内嵌凭据、query 或 fragment 的 HTTP(S) URL；容器中的 localhost 指容器自身，不是宿主机。

SWE 批次准备将模型域名和明确配置的搜索域名写入 Agent 网络白名单，随任务冻结；所有 Agent 使用同一任务网络策略。非批次任务必须预先将该域名列入 `task.toml` 的 `agent.allowed_hosts`，否则加载失败。不会开放任意互联网访问。Exa/Brave 使用原生 provider 固定 API 地址。

二进制需从包含搜索接线修复的 BKCrab 源码重新构建，再用 `snapshot-bkcrab` 保存来源；旧二进制不支持这些参数。新实验显式引用新 binary/provenance，不覆盖历史实验产物。

本地完整链路检查：

```sh
.venv/bin/python scripts/check-bkcrab-search.py /absolute/path/bkcrab-eval logs/search-check-new
```

检查使用真实 eval 二进制、原生 SearxNG provider 和本地确定性模型/搜索 HTTP fixture。通过标准为模型看到工具、搜索 HTTP 被调用、结果进入下一轮模型输入、进程成功退出。这不是公网搜索连通性、搜索质量或真实模型能力验证。

2026-10-15：基于 BKCrab `1fbd96f` 加本地修复构建，产物 `.cache/bkcrab-search-20261015/`；验证证据 `logs/bkcrab-search-20261015-check/validation.json`。当前正式实例未配置原生搜索后端；历史记录中的网络访问为 `web_fetch` 与 `exec` 调用 Camoufox，不能当作原生搜索后端配置。30 题实验尚未切换到新构建或重新启动。

验证结果：平台全量 Python 回归 295 passed、10 skipped；`go test ./cmd/bkcrab-eval ./internal/toolproviders/...` 通过；Ruff 和 diff 检查通过。本地二进制链路检查观察到 2 次 fixture 模型请求、1 次搜索请求，工具可见且结果进入后续模型输入。未验证公网搜索后端、真实模型选用搜索的行为或搜索质量。

## 2026-10-15 Exa 启用记录

- 已通过本机环境文件配置 `EXA_API_KEY` 并重新加载 console 服务；平台基线为远端最新 `15fecd2` 加本地搜索与冻结修复。BKCrab eval 基线为远端最新 `1fbd96f` 加本地搜索修复，尚未提交发布。
- 真实 Exa 链路验证：`logs/bkcrab-exa-live-20261015/validation.json`。真实 eval 二进制执行 1 次搜索，返回 10 个结果链接，并进入下一轮 fixture 模型输入；不代表真实模型主动选择搜索或搜索质量结论。
- 回归：Python 296 passed / 10 skipped；Go eval 与 toolproviders 包测试通过。
- 控制台冻结现在同时复制 dirty source 的证据目录，避免丢失 `changes_directory`。本机 systemd 服务已配置代理环境，供 SWE 准备阶段访问 GitHub。
- 新实验配置：`experiments/swe-new-30-exa-20261015.json`；控制台记录 `964c6ae458634ed4a37791c1ba220ccc`；输出 `artifacts/web-964c6ae458634ed4a37791c1ba220ccc`。
- 保留原 30 题及排除历史 101 题的选择证据；BKCrab 和 DSH Standard 各 30 次尝试，concurrency=2，每批 3 题，assist 模式。BKCrab 新增原生 Exa 工具，DSH 保持原工具配置；不能把差异全部归因于模型能力。
- 原失败记录保持原样。本次只部署测评平台及 eval 构建，没有修改正式 BKCrab 网关配置。

## 平台 relay 修订

当前新实验使用 `transport: platform-relay`：BKCrab 原生 Exa provider → `http://127.0.0.1:18765/search/exa` → 平台提供的 `live_model.py` relay → Exa。与 DSH 的 relay 部署方式一致，运行在测评容器内；它是转发与证据采集机制，并不构成防任意 shell 绕过的独立安全边界。此前 `direct` 版本仍保留用于历史追溯。

Exa 密钥由 relay 启动时读取并删除文件，BKCrab eval 仅使用 `relay-only` 占位符。relay 固定向 Exa 上游转发，不接受自定义上游 URL，记录 `search_requests.jsonl`、`search_results.jsonl` 和 `search_responses.jsonl`；三者以 request_id 关联，包含结果与耗时，不记录鉴权头。

真实验证：`logs/bkcrab-exa-relay-live-20261015/validation.json`，`platform_relay_verified=true`。Go eval/provider 测试通过，Python 299 passed / 10 skipped。构建来源为 `.cache/bkcrab-exa-relay-20261015/provenance.json`。

新配置 `experiments/swe-new-30-exa-relay-20261015.json`，新记录 `5fcada8ef0f94e1b8a39b7899c3de780`；旧记录 `964c6ae458634ed4a37791c1ba220ccc` 因操作员切换转发路径在准备阶段停止，未产生 Agent 尝试，不是 Agent 失败。

本次按用户要求不启用 JEV，不新增作弊审计；OpenRouter 凭据仅存放于本机私有环境文件，等待另一设备的实现提交后再接入。

最终启动记录：`7935e1eb71404d80919ceb40a2bbabfb`；此前 relay 准备记录因搜索响应统计隔离修复由操作员停止。最终真实验证为 `logs/bkcrab-exa-relay-live-20261015-v2/validation.json`；搜索响应独立写入 `search_responses.jsonl`，避免计入模型 Token 完整性统计。修订后 35 项相关回归通过。
