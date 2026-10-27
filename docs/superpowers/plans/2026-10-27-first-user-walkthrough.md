# 首次使用流程验收与修复

目标：按新用户顺序实走独立工作区，修复已复现的接入、模型、实验、对比与诊断障碍。依据既有本地单用户设计及用户“发现问题直接修复”的授权，直接在当前会话实施，不重新进行架构审批或委派代理。

环境：Linux Docker，独立 `/tmp/harness-onboarding-e72h`，端口 18765；原有服务和未提交文件保持不变。使用锁定依赖的新虚拟环境，机器级依赖缓存复用须明确记录；模拟模型与本机假 API 不作为真实模型能力证据。

- [x] 从源码副本执行 `uv sync --locked --python 3.12` 和 `doctor`，打开空白工作台。
- [x] 接入文档示例，实际构建自有 Agent 和内置基线；修复离线配方无法由页面运行的问题。
- [x] 检查空状态、基础接入与进化配置的顺序、模型设置和 benchmark 默认选择；用 UI 回归覆盖已复现问题。
- [x] 浏览器完成配置、预检、提交、逐题对比与创建诊断案例；模型使用本机测试端点，单独记录诊断运行时前提。
- [x] 专项 Python/Node 测试与实际 Docker 回测，更新部署/接入文档和体验验收记录，核对原始文件哈希后提交。

首批修复文件：`console.py` 的 Submission 模型模式、`static/integrations.js` 的首次接入顺序/模式/预检，`static/app.js` 与 `index.html` 的空列表引导；回归放在 `tests/test_local_comparison_flow.py` 和新的 `tests/onboarding_ui.test.cjs`。后续问题按复现证据补入本记录，不扩展无关功能。


实际记录、修复范围、实验编号及验证边界见 [首次使用实走结果](../../local-platform-validation.md#2026-10-27-首次使用实走结果)。最终常规 Python 全量 517 passed / 22 skipped，Docker 产品流程等专项 56 passed，轨迹专项 18 passed，UI 36 passed。诊断使用本机确定性执行器，只验证产品流程；不宣称真实模型推理或父子复评效果。
