# 平台 Agent 执行边界

平台复用现有 Guardian 多轮 JSONL 驱动、BKCrab/DSH 原生循环。`platform_agents.executor.execute` 负责可信角色、独立输出目录、资源准入、超时及结果记录；它不会把一次模型请求包装成完整 Agent。原始动作、模型响应与容器日志继续保留。

Guardian 的模型入口绑定 `guardian`，原生改进绑定 `improvement`，回归规划绑定 `diagnosis`。模型池可分别绑定改进和诊断模型；未指定时兼容已有执行 Agent 模型绑定。配置在候选创建时写入适配器参数快照，密钥只保留引用。各次执行分别计数，调用方自报 `purpose` 或 `role` 不能改变入口角色、模型或预算；耗尽后拒绝后续请求，未知美元费用保持未知。Integration 角色使用同一个执行请求契约，由来源接入流程调用。

Guardian `exec` 只允许白名单只读命令，不允许脚本、写文件、管道、重定向或 `kill`。文件读取限于非密钥 Agent 日志，`ps` 不暴露环境变量或命令参数。它可以调查并提交 `final` 终止建议，是否终止及资源归属由现有 supervisor 核验。即使只读 exec，现有保守标记仍将尝试标为 assisted，不能混入 autonomous 指标。直接写日志模拟恢复的旧测试已替换为真实只读检查测试。

原生改进在 Docker 中使用独立 HOME、可写产品 worktree、只读原始证据；回归规划的产品 worktree 只读。模型通过容器内策略 relay 调用；不支持的协议被拒绝。平台仍保留显式配置的自定义命令适配器；这是本机用户选择的可信扩展，不因填写角色字段自动成为安全沙箱。角色包装层不能认证任意第三方 harness 的内部工具预算或旁路网络覆盖。

每次执行的 `platform-request.json`、`platform-result.json` 和原生 `model-policy.json` / `policy-requests.jsonl` 可追溯角色与策略。超时和取消不依赖模型作出同意；清理仍按该次执行的唯一容器名或监督器拥有的资源 ID 操作。

## 本机原生执行器准备

首次使用 Integration Agent 或原生 RSI 前，在本机按 [部署说明](local-deployment.md) 准备 BKCrab/DSH 旧原生构建产物，再运行 `python scripts/prepare-rsi-runtime.py --root "$PWD"`。它将 `.cache/bkcrab-eval` 和 `.cache/dsh-bundle03/runtime.tar.gz` 打包为 Docker 镜像，并把实际 image ID 写入 `.cache/rsi-native.json`。然后在 RSI 页面保存执行 Agent 设置，在模型池绑定 `integration`（接入）及所需 `diagnosis` / `improvement` 模型。

当前平台执行器安装保留这条兼容路径，需要本机已有构建产物；安装内置测评 Release 不会隐式替换平台执行器镜像。未就绪时页面报出原因，常规 recipe 导入、控制实验不依赖该运行时。此限制与“未知 GitHub 仓库必然一键接入”不同，平台不会作后一种承诺。

Integration 读取 `/source` 的固定源码，只读挂载；接入候选写入独立 `/workspace`，不修改开发者 checkout。生成结果交给确定性 schema/构建/运行检查；冲突、过期结果和失败证据保留。默认最多 50 次模型请求、30 分钟执行时间，实际请求数可受用户更低预算限制；独立于被测实验的调用额度。真实 Docker fixture 验证了 readonly 挂载、可信角色和耗尽拒绝，但不代表任意第三方 Agent 的网络旁路已经封闭。
