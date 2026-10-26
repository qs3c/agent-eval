# P2 实施与验证记录

日期：2026-10-26。T07 标准配方/Release、T08 四种内置 Agent 与 T09 benchmark 生命周期已经实现；T10 模型策略、T11 完整产品流程仍在实施，不能将 P2 标记为整体完成。

## T07 标准接入

新增 `integrations/{schema,builder,store,commands,conformance}.py` 和通用 Recipe Agent。CLI/Python 使用同一请求/响应协议，Python callable 只在容器中导入。构建通过 Manager 排队；冻结 SHA、配方及 patch，patch 冲突停止，源码 checkout 不修改。构建使用 digest 镜像、独立 eval 副本、非 root、无宿主凭据/socket 的容器；产物和 profile 共同决定不可变 Release 身份。

内部源码链接默认拒绝；配方显式打开 `materialize_internal_links` 时只物化归档内目标，拒绝越界、循环和链接路径覆盖；最终 Agent bundle 不允许链接。它不提供任意仓库的自动安装保证。使用与完整示例见 [Agent 接入](agent-integration.md)。

真实验收：在隔离目录及临时 HOME 下，经 CLI 配方→Manager→构建容器→Release 目录→实验队列→Harbor 跑两个 controls。CLI 和 Python 两种入口各取得两个成功结果，mean_on_scored 为 1.0；每种入口都确认容器实际建立后取消另一轮实验，终态 cancelled、自有容器 0、资源预留全部释放。没有模型 API 调用。

测试发现并修复了启动附近取消时 Harbor 网络侧车遗留的问题。原生执行现在在尝试结束和 Job finally 中按精确项目回收，并在确认后释放预留；Manager 为原生清理保留最多 120 秒等待。首次失败记录 `t07c-tests.log` 保留；核对对应测试 Worker 已退出后，仅清理该次测试的容器与网络并释放其预留，记录 `t07-cancel-reconciliation.log`。修复专项回归 **13 passed，106.61 秒**（`t07d-tests.log`）。

Conformance 独立保存证据 hash，Runnable 不等于 Comparable。缺模型角色/网络完整覆盖时 Comparable 明确不通过，后续由 T10 接线；Diagnosable 要求模型请求（原生 hook 或中继）以及原生工具调用和结果。fixture 不因能解 controls 就被认定为真实模型能力提升。

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

收尾检查：来源根目录约束、嵌套 Git 仓库内 patch 隔离、同输入不同产物、离线镜像 Docker 身份校验及 conformance 重复题目拒绝已覆盖。真实 CLI/Python 构建与取消复验 **6 passed，106.05 秒**（`t07-final-tests.log`）；最后配方/构建/证据定向 **9 passed，0.28 秒**（`t07-closure-tests.log`）。这些是后续修正的定向验证，不改写前述全量快照数量。

## T08 执行记录

Hermes 固定源码构建成功，依赖由 `uv.lock` 导出并逐项 hash 校验，产物携带 Python 3.14。首次构建发现 `/tmp` 禁止执行，将构建工具留在独立构建副本中；首次运行发现配方的 `PYTHONHOME` 污染平台模型中继，修复为仅对子 Agent 命令应用配方环境。两次失败原始日志均保留，未改写成成功记录。

OpenCode 固定源码以 Bun 1.4.4 和冻结 lock 构建 CLI 2.1.4，关闭浏览器 UI 打包，保留原生 standalone loop。Hermes/OpenCode 均完成两个文件 controls，零错误、各两个评分 1.0。固定 mock 响应驱动真实原生工具，不调用付费模型，因此这些仅是协议/执行证据。

Hermes/OpenCode 共用真实验收 `tests/test_builtin_docker.py`：各自再运行两个 controls、验证来源/标准响应/模型请求/原生工具证据，再等待实际容器建立后取消 20 次尝试的实验；终态 cancelled，自有容器为零，资源预留全部释放。**2 passed，156.50 秒**（`t08-hermes-opencode-acceptance.log`）。对应 Runnable 与 Diagnosable 通过，Comparable 仍不通过。定向配方/环境/事件/Release/证据测试 **13 passed，0.44 秒**（`t08-contracts2.log`）。

BKCrab 当前源码已包含 eval 入口；新的配方仅添加构建/协议包装，不重复叠加旧 eval patch。默认 Go 代理在验证服务器不可达，构建经 Manager 取消后改用配方中显式记录的 `https://goproxy.cn`，依赖版本及 `go.sum` 校验保持不变。随后发现编译临时输出超过 `/tmp` 限额，将 `GOTMPDIR` 放入受控构建副本后构建通过。实际运行/证据/取消验收 **1 passed，58.21 秒**（`t08-bkcrab-acceptance.log`）。

DSH 保留现有 npm `0.2.1-alpha.2` 基线，并查到对应官方 tag 的真实 commit `d743267388641bc76f17c45ce8b4c231aed1d32c`。实际发现 tag 与包的 preset 提示不同；两份 hash 与不一致结果保留于审计，不覆盖发布包提示。配方明确 `registry-package` 来源，不把 npm 发布包伪装成本次源码编译。打包时将包内 CLI 链接物化为普通文件，拒绝逃逸链接。实际运行/模型协议/取消验收 **1 passed，86.08 秒**（`t08-dsh-acceptance.log`）。DSH 保留原生 session events 与请求上下文，但当前严格工具事件判定不授予 Diagnosable；不能把已有原始轨迹等同于完整规范事件覆盖。

迁移前后行为对照：使用已有 DSH Native bundle，以及同一新 BKCrab 二进制的旧 Native 入口，与各自 Recipe 入口组成四 Agent、两 controls 的同模型 fixture 实验。**8/8 结果通过、零错误**（`t08-legacy-comparison.log`；原始实验 `artifacts/t08-legacy-comparison`）。这证明控制任务与入口迁移行为一致，不证明所有任务或真实模型性能等价。

T08 最终定向回归 **19 passed，0.59 秒**（`t08-final-contracts.log`）；完整离线及既有 Docker 开关回归 **464 passed、7 skipped，329.50 秒**（`t08-full-tests.log`）。其中四个内置 Docker 案例默认跳过，已在上述独立指定 Release 的真实验收中分别通过，不能把默认跳过误算为验收通过。四基线均独立完成真实构建、协议运行与取消回收；Comparable 完整覆盖仍待 T10，P2 整体未完成。
# T10 / T11 补充记录

模型策略：`t10-full-fixed.log` 477 passed、7 skipped；四内置 Agent 实际策略控制实验 `t10-builtin-policy.log` 4 passed。入口固定可信角色、模型与参数，记录请求预算与未知费用，原有未提交 relay 文件保持原哈希。

本地产品流程：`t11-full.log` **486 passed、3 skipped / 648.99 秒**。自定义 fixture 通过实际 Docker 构建，再由 Web API 与内置 BKCrab 共同选择两道控制题、预检、冻结与运行；四次尝试零错误且均通过评分，两版本通过运行检查，保留策略证据。fixture 最初缺少工具声明被模拟端拒绝，修正后完整复验通过，失败日志未删除。

浏览器另核验 Agent 选择、四次尝试预检、未知费用/覆盖说明，以及修改题目数后禁止直接提交。测试未配置真实 API Key、未调用付费模型、未公开发布 PR、未部署生产服务。四内置源构建的固定镜像已缓存，本次不证明新主机网络下载可用；通用 GitHub URL 修复和 RSI 闭环仍按后续阶段推进。
