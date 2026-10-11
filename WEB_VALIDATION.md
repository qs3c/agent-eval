# 本机 Web 控制台验收

日期：2026-10-11。实现范围：本机单用户，创建实验、查看进度、对比结果；未调用新的付费模型。

## 自动检查

- `.venv/bin/pytest -q`：36 passed（保留原有 23 项，新增控制台与 API 测试）。
- 测试覆盖：配置白名单、任务选择、输入快照、原子领任务、进程启动失败、关闭时终止所属进程、服务单实例锁、重启中断、真实模型启动策略、模板与证据路径边界、跨站写入、请求大小、日志脱敏、缺失及损坏报告、比较条件。
- `ruff check src tests scripts/*.py` 通过；`node --check src/agent_eval/static/app.js` 通过。
- `uv build --wheel` 成功；wheel 包含 index.html、app.js 和 style.css。

## 真实离线评测

通过浏览器创建 `web-console-validation`，模板为 `controls`。未替换执行器或模拟运行状态，实际由现有 CLI → Harbor → Docker 执行。

- 运行 ID：`8b1f85e7a4d443069c3b2bb09e165796`。
- 产物：`artifacts/web-8b1f85e7a4d443069c3b2bb09e165796/report.json`。
- 两个 Agent × 两个任务：4/4 结束；positive 两题 reward 均为 1，negative 两题均为 0；0 errors、0 missing。
- 关闭空闲服务后重新启动，该运行仍可读取，状态与结果保留。
- 与历史 `controls-final` 比较：条件一致，逐任务对比可用；与真实 SWE 运行比较：显示 benchmark、任务哈希、模型和超时等差异。

## 浏览器验收

- 实际操作创建表单、提交、进度与终态刷新、历史列表、搜索空状态、键盘 Tab 导航、同条件与不同条件对比。
- 检查桌面及窄屏响应式布局，表格在容器内部横向滚动，窄屏页面本身无横向溢出。浏览器 viewport 覆盖的逻辑宽度与截图像素存在缩放，窄屏检查以实际 DOM 宽度及截图为准。
- 浏览器开发日志未发现 error/warn。
- 截图保存在本地 `.cache/web-console/result.jpg`，不提交仓库。

## 限制

真实模型执行入口已有开关和校验测试，本次未重新调用付费模型。历史日志显示原始产物位置，不提供任意文件下载。SWE 官方复评结论不与 Harbor 内置报告混同。服务重启不会自动检查或清除所有 Docker 遗留资源；中断后需要人工检查并使用 --recover 恢复。首版未做多人、远程部署或负载测试。
