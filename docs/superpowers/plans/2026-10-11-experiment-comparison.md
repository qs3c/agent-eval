# Experiment Comparison Implementation Plan

**Goal:** 单实验多 Agent 总览、逐题下钻、可追溯的数据集版本与评分口径。
**Architecture:** 复用 Console.detail 的任务结果；comparison.py 负责纯统计，comparison.js 负责实验选择和矩阵；traceCard 继续显示原生证据。
**Tech Stack:** Python / Pydantic / FastAPI / 原生 JavaScript / pytest。
**Spec:** docs/superpowers/specs/2026-10-11-experiment-comparison-design.md（用户已确认）。

## Global Constraints
- 不调用付费模型，不运行新的 benchmark，不改已有实验产物，不提交其他工作区改动。
- 未知量保持未知；未评分尝试留在计划分母；不同题集不混算。

### Task 1: 统计和版本契约
- [x] 新增 tests/test_comparison.py，覆盖两题不同结果、重复尝试、缺失/异常、额外尝试、非二值指标与未知用量。
- [x] config.py 增加 DatasetIdentity / EvaluationIdentity 和 score_kind；comparison.py 提供 summarize(experiment, trials, score_kind, terminal)。最终分要求每题每次完整评分；否则 resolved 仅提供已解决次数/计划次数的暂定比例。
- [x] Console.evidence 保留任务结果的 trial ID 和原生用量；comparison API 返回摘要与版本信息。未知历史版本不填猜测值。
- [x] tests/test_console.py 和 test_web.py 验证元数据冻结、版本不一致及只读接口。

### Task 2: 页面
- [x] comparison.js 实现单实验选择、整体指标表、Agent 两两选择、逐题矩阵与任务筛选；一个任务默认展开。
- [x] 以 task + agent 过滤尝试记录，按需请求两份轨迹；异步结果检查 epoch 与局部版本，防止旧请求覆盖新选择。
- [x] 从运行详情链接到 #compare/run-id；旧批次入口保留并正确命名，显示版本。

### Task 3: 验证与交付
- [x] 运行 .venv/bin/python -m pytest，JavaScript 语法与 Python lint 检查。
- [x] 用浏览器检查现有单题与离线多题数据、窄屏及切换任务/Agent/实验；不提交运行表单。
- [x] README.md 记录配置示例与统计规则，WEB_VALIDATION.md 记录实际验证结果。
