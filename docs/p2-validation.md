# P2 实施与验证记录

日期：2026-10-26。T07 标准配方/Release 与 T09 benchmark 生命周期已经实现；T08 内置四种 Agent、T10 模型策略、T11 完整产品流程仍在实施，不能将 P2 标记为整体完成。

## T07 标准接入

新增 `integrations/{schema,builder,store,commands,conformance}.py` 和通用 Recipe Agent。CLI/Python 使用同一请求/响应协议，Python callable 只在容器中导入。构建通过 Manager 排队；冻结 SHA、配方及 patch，patch 冲突停止，源码 checkout 不修改。构建使用 digest 镜像、独立 eval 副本、非 root、无宿主凭据/socket 的容器；产物和 profile 共同决定不可变 Release 身份。

内部源码链接默认拒绝；配方显式打开 `materialize_internal_links` 时只物化归档内目标，拒绝越界、循环和链接路径覆盖；最终 Agent bundle 不允许链接。它不提供任意仓库的自动安装保证。使用与完整示例见 [Agent 接入](agent-integration.md)。

真实验收：在隔离目录及临时 HOME 下，经 CLI 配方→Manager→构建容器→Release 目录→实验队列→Harbor 跑两个 controls。CLI 和 Python 两种入口各取得两个成功结果，mean_on_scored 为 1.0；每种入口都确认容器实际建立后取消另一轮实验，终态 cancelled、自有容器 0、资源预留全部释放。没有模型 API 调用。

测试发现并修复了启动附近取消时 Harbor 网络侧车遗留的问题。原生执行现在在尝试结束和 Job finally 中按精确项目回收，并在确认后释放预留；Manager 为原生清理保留最多 120 秒等待。首次失败记录 `t07c-tests.log` 保留；核对对应测试 Worker 已退出后，仅清理该次测试的容器与网络并释放其预留，记录 `t07-cancel-reconciliation.log`。修复专项回归 **13 passed，106.61 秒**（`t07d-tests.log`）。

Conformance 独立保存证据 hash，Runnable 不等于 Comparable。缺模型角色/网络完整覆盖时 Comparable 明确不通过，后续由 T10 接线；Diagnosable 要求原生模型请求、工具调用及工具结果。fixture 不因能解 controls 就被认定为真实模型能力提升。

## T09 Benchmark 生命周期

新增显式 registry 及标准 Harbor/SWE 生命周期，批次准备/评分/释放通过插件边界，返回并保存资源依赖引用；最终镜像 ID 与所有权仍由现有生命周期账本核验。普通 resolved 指标不隐式触发 SWE。现有 SWE 数据准备、版本化官方复评和完整重复导出保留。

新增 `benchmark list/inspect/select`，使用稳定哈希排序抽样，支持 seed、历史任务排除、候选内容 hash 与最终任务列表冻结。候选变化、重复名称、题量不足或不支持的运行时能力会拒绝。CLI/Web 队列继续冻结选择记录。使用见 [Benchmark 接入](benchmark-integration.md)。

真实 Recipe controls 在 Agent 执行时断言 `/tests/test.sh`、`/solution/solve.sh` 不存在；全部通过。此结果证明该执行路径的阶段隔离，不证明任意自定义任务代码没有主动泄漏答案。

## 验证快照

- T07 首轮 CLI/Python 构建运行：5 passed，75.01 秒；取消专项随后发现上述侧车问题，不能用首轮结果替代取消验收。
- 全量 T07：449 passed、1 failed、3 skipped，失败是新 CLI 注册过早引入 Pydantic 导致无依赖 doctor 失效；已恢复延迟加载。
- doctor/registry/selection/batching/resources/recipe 定向：44 passed，1.04 秒（`t09a-tests.log`）。
- 全量离线及 Docker：**455 passed、3 skipped，320.43 秒**（`t09b-full-tests.log`）。开启 `INTEGRATION_DOCKER_TESTS=1 CLEANUP_DOCKER_TESTS=1 GUARDIAN_DOCKER_TESTS=1 RSI_DOCKER_TESTS=1`。

所有 Linux 验证在 `/tmp/harness-p1-zUBzJWtE`，使用 P0 锁定的 Python 3.12/Harbor 环境并通过 `PYTHONPATH=$PWD/src` 加载当前源码。没有修改生产项目、部署 BKCrab、推送仓库或创建公开 PR。Windows 本地仅执行编译检查和 diff 检查，不当作 Linux Docker 认证。

## 上游核对（T08 进行中）

已核对官方仓库，固定观察到的源码：OpenCode `ed74ec82ce1fe3a36da6c0815a935473ef4bb0fb`（dev，CLI 2.1.4）、Hermes `9b904dcc22c23fe8b82834339bbdd96495072e59`（main，正常运行要求 Python 3.14）、DSH `22ddee3792402c3e70ec5f86a63c3e3e895a2f60`（master）。三者仓库声明 MIT。当前平台已有 DSH 仍锁定 npm `0.2.1-alpha.2`，不会把观察到的最新源码冒充已验收版本。

依据：[OpenCode 固定源码](https://github.com/anomalyco/opencode/tree/ed74ec82ce1fe3a36da6c0815a935473ef4bb0fb)、[Hermes 依赖与 Python 约束](https://github.com/NousResearch/hermes-agent/blob/9b904dcc22c23fe8b82834339bbdd96495072e59/pyproject.toml)、[DSH 固定源码](https://github.com/deepseek-ai/deepseek-harness/tree/22ddee3792402c3e70ec5f86a63c3e3e895a2f60)。这些是来源核对，不是四种内置 Agent 已通过接入验收的证明。

收尾检查：来源根目录约束、嵌套 Git 仓库内 patch 隔离、同输入不同产物、离线镜像 config digest 校验及 conformance 重复题目拒绝已覆盖。真实 CLI/Python 构建与取消复验 **6 passed，106.05 秒**（`t07-final-tests.log`）；最后配方/构建/证据定向 **9 passed，0.28 秒**（`t07-closure-tests.log`）。这些是后续修正的定向验证，不改写前述全量快照数量。
