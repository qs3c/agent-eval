# 本机源码跟踪与 RSI 合并后复评

平台是本地单用户工作区。GitHub 只提供源码和 PR 状态；本机 Manager 持有执行权，不需要服务器 webhook、多租户账户或远程执行集群。来源记录在 `.workspace/sources.sqlite`，配方输入、构建、Release 和接入证据分别保留。

## 版本何时改变

来源的三个指针含义不同：latest observed 是最近查询的分支 SHA；latest admitted 是已经通过隔离构建和运行检查的 Release；recommended 是有真实模型同条件复评支持的推荐版本。普通 push 与 RSI 合并都必须经过准入，失败不改变已有可运行版本。旧版本不会冒充最新源码。新实验或下一轮才采用新版本，运行中的 Plan、任务和产物不会被替换。

默认每分钟查询已启用来源；失败指数退避，最多一小时。重复 repo/ref/SHA 观察不会重复构建。连续更新会取消尚未开始的旧构建；正在执行的构建保留结果。force-push 重新出现的 SHA 可以复用已准入产物，未执行的旧记录则建立带重试代次的工作。可暂停自动跟踪，手动刷新仍是明确操作。

```bash
uv run agent-harness-eval source add --repository https://github.com/OWNER/AGENT --ref main
uv run agent-harness-eval source list
uv run agent-harness-eval source revisions --source-id SOURCE_ID
uv run agent-harness-eval source refresh --source-id SOURCE_ID
uv run agent-harness-eval source pause --source-id SOURCE_ID
uv run agent-harness-eval source resume --source-id SOURCE_ID
```

也支持显式本机 Git 绝对路径。URL 不接受内嵌凭据；当前自动拉取入口面向公共 GitHub 仓库。私有仓库可先在本机准备干净 clone，再按本机来源使用。每个构建固定完整 commit SHA，禁用 Git hooks，不在用户原 checkout 中应用接入 patch。

## 自动接入与修复

先找标准配方或用户指定配方，再在独立派生目录应用 patch、Docker 构建、执行两题隔离控制检查。这里只授予有证据支持的 Runnable 等级，不把一次 smoke 当成完整 Comparable 认证。模型调用覆盖缺口继续展示。

未知项目显示“需要配方”。用户可提供配方，或启动 Integration Agent。后者复用已安装的 BKCrab/DSH 原生多轮平台执行器，独立 `integration` 模型绑定、home、证据目录、超时和最多 50 次模型请求。上游源码只读挂载，候选只写接入配方和 patch；结果通过 schema、仓库/SHA、路径和来源检查后，再进入普通构建与 conformance。生成期间源码更新会拒绝采用过期候选。模型报告成功本身没有准入效力。

```bash
uv run agent-harness-eval source repair --source-id SOURCE_ID --recipe /absolute/repair/recipe.json
# 先安装平台原生运行时、保存执行器设置、配置 integration 模型绑定。
# 此命令明确同意一次有界模型调用；费用仍可能未知。
uv run agent-harness-eval source assist --source-id SOURCE_ID --confirm-paid
```

Web 的“接入 Agent”页面提供相同操作。Web 模型调用还需 `serve --allow-live`。生成失败、构建失败和 patch 冲突保留证据，可明确提交下一次修复；默认不无限调用模型自行重试。安装运行时见 [平台 Agent](platform-agents.md)，配方标准见 [Agent 接入](agent-integration.md)。当前 DSH 内置基线使用 registry-package，不能用它假称已从新源码构建；源码进化须提供 `artifact_origin: source` 配方。

## 普通更新与 PR 合并的下一轮

闭环创建时冻结题目、配置、模型策略、基线 Release，并设置轮次、总尝试数和等待时间上限。未指定候选时跟踪普通源码更新；指定已发布 PR 的 RSI 候选时，只读查询 GitHub。发布仍走已有显式发布操作，不自动推送或合并。

```bash
uv run agent-harness-eval cycle create --source-id SOURCE_ID \
  --evaluation /absolute/experiment.json --max-rounds 2 --max-attempts 20
uv run agent-harness-eval cycle create --source-id SOURCE_ID \
  --candidate CANDIDATE_ID --evaluation /absolute/experiment.json \
  --max-rounds 1 --max-attempts 20 --max-seconds 86400
uv run agent-harness-eval cycle list
uv run agent-harness-eval cycle refresh --cycle-id CYCLE_ID
uv run agent-harness-eval cycle stop --cycle-id CYCLE_ID
```

真实模型需 `--confirm-paid` 和显式每次尝试请求预算；次数、尝试数与时间是可执行上限，美元硬预算仍不支持。当前闭环评测只接受 autonomous 配置。预算以提交次数计，即使尝试失败也占用额度。停止会取消该闭环尚未结束的实验，不删除证据。

GitHub 返回真实 `merge_commit_sha`，merge/squash 都重新构建和准入，不复用合并前 candidate 产物。合并 SHA 可以独立固定，不把它伪装成后来又有 push 的当前分支 tip。PR 关闭未合并则停止；GitHub 暂时离线只重试查询。开放 PR 的 base/head 漂移使旧验证过期，保留旧验证文件；需要从最新基线创建并验证新候选，不能把旧结论升级为 ready。

每轮保存真实 Plan、源码 SHA、Release、评分结果和证据路径。缺失/未评分/异常不能算改善；退化或证据不足进入“等待诊断”，不继续消耗预算。模拟/offline 控制实验只验证链路，即使满分也不更新 recommended。真实同条件非退化可以更新推荐，但结论仅适用于该冻结题目集，不能当成完整模型覆盖证明或统计显著提升。合并后复评完成后，后续诊断和 PR 发布仍需显式发起。

所有来源观察和闭环状态均可在“接入 Agent”页面查看；RSI 候选页显示验证过期原因，并链接合并后复评入口。恢复/备份应包含 `.workspace`、`.rsi/cycles` 及关联原始证据，见 [迁移与备份](workspace-migration.md)。
