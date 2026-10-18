# 四 Agent SWE30 Implementation Plan

**Goal:** 修复用量与补丁交付，接入 OpenCode、Hermes，冻结未暴露的 30 道 SWE Verified 题并启动 120 次尝试。

**Architecture:** 保留四个 Agent 原生循环；平台负责冻结、模型代理、用途分离统计、隔离环境和官方评分。先做独立冒烟验证，再启动正式实验。用户已在本对话批准步骤 1 和 2。

**Tech Stack:** Python、Harbor 0.24.0、Docker、SWE-bench 4.1.0、官方 DeepSeek。

**Spec:** 本对话中用户批准的四 Agent 新题方案。

## Global Constraints

- 不改历史 artifacts，不覆盖现有未跟踪配置，不把环境恢复计入 Agent 成绩。
- 同模型、同题、同资源与网络审核政策；冻结源码、运行包、任务、模型参数。
- 正式任务排除历史实验和冒烟中已经暴露的题目；每题独立会话与持久化目录。
- 主 Agent、搜索辅助、审核用量分开；未知费用不填零。
- 不放宽官方测试或通过标准；补丁无法准确重放时作为基础设施错误。

## Task 1: 用量统计
- [x] 修改 `traces.py` 与 `resources/live_model.py`，按用途配对、统计，确保每条拒绝路径有结束记录。
- [x] 在 `tests/test_traces.py` 等验证搜索拒绝不会污染主模型完整性，缺失主模型响应仍未知。
- [x] 用冻结 934e63 运行日志只读重算，核对主模型计数。

## Task 2: 补丁重放
- [x] 核对 `resources/capture_patch.py`、`resources/official_runner.py` 和 `benchmarks/swe.py` 的环境差异与回退应用。
- [x] 构造环境预置修改 + Agent 修改的临时 Git 仓库回归测试，禁止反向应用并保留证据。
- [ ] 在独立输出中验证旧失败样本，保留原始历史结果；测试标识问题单独记账。

## Task 3: 新 Agent 接入
- [x] 新增 OpenCode/Hermes 适配器与可冻结运行包，复用本地模型代理；原生系统提示和工具保持可审计。
- [x] 验证版本、模型路由、隔离 HOME、返回码、轨迹和用量；测试不读取真实凭据。
- [x] 使用正式集合外的简单任务分别做 live 冒烟，验证实际工具调用与审核。

## Task 4: 冻结与启动
- [x] 生成带来源记录的历史排除集合，按仓库分层固定随机种子选取 30 题。
- [x] 写入 selection、records、配置及哈希，验证与所有排除题零交集。
- [x] 正式启动前确认磁盘、Docker、无冲突运行、120 次尝试及四个冻结 Agent。
- [ ] 通过 Console 创建可见实验并启动，验证进入实际模型调用，报告 run ID、状态与限制。

## 当前验证证据

- 全量测试首轮 402 passed、12 skipped；后续增量与最终全量结果见本次记录。
- 独立 Docker 补丁控制验证：环境初始差异不混入 Agent patch；恢复后正向应用得到相同树；重复应用失败且不反向撤销。历史任务 astropy 参数化测试名称不匹配仍单独保留，不更改评分规则。
- 四 Agent 初轮 live smoke：`43f7f5aba97547d8a3bceab3d0b28abf`。文件检查四者通过；Hermes ddgs 网页提取不可用，故该结果不能视为完整集成通过。
- Hermes 改用原生 Exa 后独立 live smoke：`df945f94e5804309b402d52270c0c87e`。
- 30 题固定 seed `2026101702`，排除历史 131 题，与排除集无交集；这只表示本平台此前未跑过，不声明训练集未见过。
- 网络审核位于平台模型入口；不同 Agent 的原生搜索供应商不同。原生工具原文和 exec/curl 访问不由该入口全面隔离，不能将审核拒绝率解释为完全杜绝外部答案。
- 平台审核文件来源：`/home/xavier/.codex/worktrees/platform-fetch-review/agent-harness-eval`，在当前工作区扩展了新 Agent 工具别名及搜索审核；未提交。

## 正式运行

- Console run：`b9642f358f4440959bbf1842d5e2dc5e`；已启动，预计 120 次；首次检查处于第 1/10 批镜像准备，完成 0/120。
- 配置：`experiments/swe-four-agent-new-30-20261017.json`；快照：`.console/inputs/b9642f358f4440959bbf1842d5e2dc5e/`；输出：`artifacts/web-b9642f358f4440959bbf1842d5e2dc5e/`。
- 四份运行包哈希已核对，selection.json 已随 Console 输入冻结。
- Hermes Exa live 复测 reward=1，无执行异常；原生搜索与提取均成功并通过入口审核。主模型 usage 完整，标题辅助请求 usage 不完整单独保留。
- 最终全量 424 passed、12 skipped；随后新增选择快照、标题用途与畸形搜索参数回归，相关集合 85 passed、1 skipped。
