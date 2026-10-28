# Hermes Web 结果判定修复与本轮补救

2026-10-18 核验发现，冻结的 Hermes `06671b70756452c9bb45c0e7297835f7728143fc` 用字符串匹配检测 Web 失败：成功短网页中的 `"error": null` 也会被判成错误，并累积 `repeated_exact_failure_warning`。搜索内容包含相同关键字时也可能误报。

修复版明确标为 `06671b7+web-result-fix.468d8dff`，本地源码提交为 `468d8dff407456cfdaeb5d870b7c0dc77750db8b`，不是未修改的上游原版。补丁保存在 [patch 文件](../patches/hermes/06671b7-web-result-classification.patch)，包含三个源码文件和行为回归测试；可在固定上游提交上应用。两个判定入口共用结构化结果检查，读取真实错误值和失败状态；真实后端错误仍判失败，原生冗余调用拒绝不累计为工具失败。未修改工具、提示词、模型路由或搜索后端。

## 已执行验证

- 新增测试在旧版上出现 7 项失败、4 项通过；修复后的相关原生回归共 52 项通过，使用上游 `scripts/run_tests.sh` 执行。
- 上游 `scripts/check` 12 项检查通过，code health 无阻塞或建议项。
- 修复包的 Python 和 `site-packages` 文件哈希与旧包逐项一致；Agent 源码仅三处文件变化。
- 补丁应用到旧 Git tree 后，重建的 tree 与修复提交完全一致。
- 真实冒烟 `7d34d082a7b345fc8f969a359988245b`：搜索成功、两次相同网页提取成功，误判失败警告为 0；平台审核均放行，模型确实收到正文。相同成功内容的第二次读取出现原生 `idempotent_no_progress_warning`，属于预期重复读取提示。
- 主模型 usage 完整，Harbor fixture reward=1，无执行异常。该结果属于集成验证，不计为 SWE 解题成绩。

## 当前正式实验

运行 ID：`b9642f358f4440959bbf1842d5e2dc5e`。

旧 manifest 与 Console 配置原文保存在运行目录 `recovery/hermes-web-result-fix/original-snapshot/`。旧运行包与 provenance 保留原文件，新包使用独立文件名。当前 manifest 追加 `unstarted_agent_upgrade` 记录并指向 `migration.json`，明确记录前后版本和哈希；这是一项有记录的冻结配置修订，不是宣称整个实验从启动起就使用同一份旧 manifest。

`completed-evidence.json`、`fixed-live-acceptance.json`、`runtime-comparison.json`、`patch-verification.json` 和 `migration-script.py` 保存补救证据。恢复时仍执行任务、模型、Agent 产物及工作台哈希检查。已恢复同一运行，跳过已有 8 条终态尝试，先运行剩余 OpenCode 题，再运行修复版 Hermes；官方复评保持关闭。

本轮 Hermes 成绩应归属于上述本地修复版。此次修复的必要性也说明，Web 冒烟验收必须核对原生工具结果、失败判定和模型实际接收内容；不能只看最终文件检查或 reward=1。
