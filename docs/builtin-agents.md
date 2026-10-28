# 内置 Agent 与独立安装

内置目录提供固定版本的配方，不代表这些 Agent 已在你的机器上安装或验收。运行 `uv run agent-harness-eval integration builtins` 查看当前配方的状态：`unavailable`（未安装或已停用）、`unchecked`（已构建但未通过运行检查）、`runnable`（有对应 Release 的运行证据）。Comparable 需要单独的完整模型/网络策略覆盖证明。

公共配方为 DSH、OpenCode、Hermes；版本与完整 SHA 以 src/agent_harness_eval/integrations/recipes/ 中的配方为准。不要把版本目录表当作已验收列表。它们均保留各自原生 harness 循环。每次运行使用独立 home，主模型指向平台中继；压缩、子 Agent、回退路径是否全部覆盖仍需策略验收。

## 构建一个条目

克隆对应官方仓库，在干净 checkout 中检出配方中的 commit。配方内的镜像均固定 digest；先按配方下载该镜像到本机 Docker。然后在平台目录运行：

```sh
uv run agent-harness-eval integration build --builtin hermes --source /path/to/hermes-agent
uv run agent-harness-eval integration list
uv run agent-harness-eval integration agent --release <release-id>
```

构建由本地 Manager 接管，使用独立源码副本、非 root 容器和资源预算。网络只用于所声明的构建依赖下载。上游 checkout 不会被 patch；失败日志保留在 `.workspace/builds/`。网络受限时可导入经过摘要验证的 OCI 镜像归档，不能用同名标签替代固定 digest。

把 `integration agent` 返回的配置加入实验 `agents`，选择模型和 benchmark 后用通常的实验入口运行。至少完成两个独立尝试，再执行：

```sh
uv run agent-harness-eval integration check --release <release-id> --run /path/to/run
uv run agent-harness-eval integration builtins
```

Hermes/OpenCode 的原生 JSONL 保留原样；派生事件记录原始文件及行号。模型请求来自平台中继，不把派生事件冒充上游原生 provider hook。未观察到的调用、未记录的费用都保持未知。

## 停用、恢复与历史证据

```sh
uv run agent-harness-eval integration disable --release <release-id>
uv run agent-harness-eval integration enable --release <release-id>
```

停用将该版本移出内置选择目录；不会删除已经被实验引用的 bundle、运行证据或其他 Agent。恢复不绕过原有运行检查。磁盘回收应遵守资源引用与保留策略，不直接删除仍被计划引用的 `.workspace/releases`。

## DSH 发布包与源码候选

DSH 配方显式记录 `build.artifact_origin=registry-package`，依赖使用平台已有 npm lock；构建保存发布包和源码 tag 的 Standard preset 两个 hash、一致性结果及模块审计。实际核对发现两者的 plan-mode 提示存在差异，因此不能把官方 tag 当作发布包的精确源码证明。运行保留现有锁定发布包，不将 tag 的提示覆盖进去。该 profile 可作为固定比较基线，不能用于声称某个 RSI 产品源码补丁已经在候选 Agent 中生效；这类验证必须使用 `source` 构建配方。