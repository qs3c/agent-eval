# 本地单用户部署与环境诊断

本页描述本地安装与完整使用入口：显式 `workspace-import`、独立后台 Manager、标准 Agent 接入、同模型对比，以及 GitHub 来源准入和合并后复评。详见[迁移说明](workspace-migration.md)、[Manager 使用说明](local-manager.md)和[源码跟踪](source-tracking.md)。未知仓库自动接入仍需配置平台 Agent，并通过确定性构建/运行检查，不能保证任意项目只填 URL 就成功。

## 支持范围

| 场景 | 当前要求 |
| --- | --- |
| 平台执行 | Linux x86_64、Python 3.12、可访问的 Linux Docker daemon、Git、uv 0.13.0 或更新版本 |
| controls | 只需平台依赖；不需要 API key、Go、Node 或 BKCrab checkout |
| DSH 旧原生构建 | 额外需要 Node 22 或更新版本、npm、锁文件；标准 Release 的工具链在容器中 |
| BKCrab 旧原生构建 | 额外需要 Go 1.25 或更新版本、含 eval 入口的 BKCrab checkout；标准 Release 的工具链在容器中 |
| SWE 准备 | 额外需要 uv 管理的 Python 3.13、数据与镜像下载空间；评分运行时另按固定构建准备 |
| Windows 原生 | 可运行只依赖标准库的 doctor 获取诊断；不支持执行实验 |
| WSL2 | 能检测并提示；尚未完成支持认证，不能用 doctor 通过代替完整验收 |

版本下限只用于预检；具体可复现运行仍使用 `uv.lock` 和 Agent 锁文件。CPU/内存诊断是环境信息，不是并发准入。默认检查 20 GiB 空闲余量，SWE 实际占用可能远大于此值。doctor 不安装组件、不拉镜像、不运行 Agent、不发送 API 请求、不启动/停止进程。

## 安装与无模型验收

### 从自己的 Agent 到逐题对比

启动工作台后，进入 **Agent 接入**：自定义 Agent 填写干净源码目录与标准配方路径（见[接入规范](agent-integration.md)）。内置 Agent 先按卡片上的命令准备固定 Docker 镜像，再点击安装；Manager 下载固定 SHA 的源码并排队构建。无需在宿主机安装各 Agent 的编译工具，但 Docker 构建会下载依赖，未知下载大小不会显示为零。构建失败保留原因，其他版本仍可使用。

进入 **创建对比**，同时选择自己的 Release 与一个内置 Release，选择 benchmark 模板、题目数及抽样种子。首次使用 `controls`、两题、模拟模型执行接入检查，不需要 API Key。点击预检可看到尝试数、磁盘余量、付费状态、模型覆盖限制；预检不发送模型请求。确认运行后，到记录详情看进度，到结果对比看逐题表现。完成后回到 Agent 接入，选择该版本和已结束实验核验运行证据。

真实模型实验先在 **模型设置** 填写 API URL、模型 ID 和 Key，服务使用 `--allow-live` 启动；创建对比时切换真实模型并选择同一个模型条目。预检后确认费用才会入队。模型、任务或 Agent 产物在预检后改变会使提交失效。题目抽样清单随实验冻结，后续更改源码、模型池或模板不会回写已有实验。

`controls` 的任务使用 `no-network`，只供离线和模拟模型检查，不能仅切换为真实模型后运行。真实模型任务的 `task.toml` 中，`[agent]` 应使用 `network_mode = "allowlist"`，并在 `allowed_hosts` 放行所用模型端点的主机名；不要改动验证器的网络策略。自定义 API URL 也必须符合任务的网络限制。预检会拦截禁网任务，但不会实际发送请求测试连通性。宿主机模型服务应监听容器可达的接口，填写对应地址；任务容器中的 `localhost` 指向容器自身。

如果此前已经启动过不带 `--allow-live` 的服务，先关闭 Web，在同一平台目录执行 `uv run agent-harness-eval manager stop`；等待 `uv run agent-harness-eval manager status` 显示 `alive=false`，再启动 `uv run agent-harness-eval serve --allow-live --port 8765`。Manager 会等待已有作业结束，不要为更改开关强行杀死正在执行的实验。只关闭浏览器或 Web 不会改变后台执行策略。

接入文档中的 `hello-harness` 是无需模型的协议示例，在“创建对比”选择 **离线 · 无模型协议检查**，单独运行两题。与 BKCrab 等模型 Agent 对比时，应接入支持 mock/live 的自己的 Agent；先做模拟模型检查，再使用配置好的真实模型。

结果页区分成绩、执行错误、评分等待、未知费用与模型策略覆盖。模拟模型成绩仅证明执行链路；当前内置版本不因运行成功自动取得完整 Comparable。GitHub URL 接入和持续版本闭环见 [源码跟踪](source-tracking.md)，未知仓库需要配方或配置好的 Integration Agent。

在逐题对比中选择题目和具体尝试，点击 **保存这次尝试为诊断案例**。这一步冻结证据，不调用模型，也不要求填写改进目标；正常执行但得分为零的记录同样可以诊断。随后按案例页提示配置平台执行 Agent 和诊断/改进模型，再创建候选运行改进。被测 Agent 的 Release 与平台诊断执行器是两套独立产物；后者首次准备步骤见 [平台 Agent 执行边界](platform-agents.md)。

先通过官方安装方式准备 Git、Docker 和 uv，让当前用户可访问 Docker。然后在用户自己的平台 checkout 中运行：

```bash
uv sync --locked --python 3.12
uv run agent-harness-eval doctor --root . --port 8765
uv run agent-harness-eval run experiments/controls.json --output artifacts/controls-p0
uv run agent-harness-eval report artifacts/controls-p0
uv run agent-harness-eval serve --port 8765
```

输出目录必须全新，重复验收换一个名字；不要覆盖历史记录。controls 是两个文件任务 × 正负对照共 4 次尝试，预期正对照两题得分 1、负对照两题得分 0，无执行错误。它证明执行链路，不证明模型质量。

如果依赖环境尚未建立，可使用 Python 标准库先诊断：

```bash
PYTHONPATH=src python3 -m agent_harness_eval.cli doctor --json
```

`doctor` 输出 schema version 1，每项包含 `check_id`、`required_by`、`status`、`evidence`、`remediation`。status 为 pass/warn/fail；任一 fail 退出码为 1，其余为 0。warn 仍需阅读，例如未认证 WSL 或未测试模型连接。参数非法非零退出。工具版本、Docker 信息读取设 10 秒超时，不输出任意工具 stderr 或密钥值。

```bash
uv run agent-harness-eval doctor --agent dsh --benchmark controls
uv run agent-harness-eval doctor --agent bkcrab --agent dsh --benchmark swe --min-free-gib 40
uv run agent-harness-eval doctor --live --api-key-env MY_MODEL_KEY --json
```

`--agent` 可重复；默认 controls。`--live` 只检查指定环境变量是否非空，不验证 URL、模型协议或 key 有效性；模型池保存的凭据应在模型设置中检查。没有 key 不妨碍 mock/controls。模型连通性与付费生成必须通过另外的明确操作验证。

## 独立准备 Agent

```bash
# 只准备 DSH，不读取 BKCRAB_SOURCE，不运行 Go。
bash scripts/prepare-native.sh dsh

# 只准备 BKCrab，不运行 npm 或 DSH 打包。
export BKCRAB_SOURCE=/absolute/path/to/bkcrab
bash scripts/prepare-native.sh bkcrab

# 保留旧调用兼容；省略参数等于 all。
bash scripts/prepare-native.sh all
```

以上为保留的原生构建入口。新的通用 Release 管理已提供“接入 Agent”页面与 `integration` CLI，支持四个固定基线和自定义配方。已有 BKCrab eval 入口无需重复应用旧 patch。DSH 已存在的旧 bundle 仍拒绝覆盖，新的 Release 使用内容身份独立保存。

平台主程序目前使用 Python 环境启动；Agent/任务执行使用 Docker。部署 BKCrab 服务到项目约定服务器时，仍须 Docker 部署并遵守既有端口与源码流程；本页不要求为运行 controls 停止 BKCrab 服务。

## Web、生命周期与私网

默认访问 `http://127.0.0.1:8765`。私网连接使用 `serve --host <可信私网IP> --port 8765`，必须给具体地址，保留 Host 白名单与同源检查。当前控制台没有账号登录，不公开暴露。

浏览器刷新、关闭页面或停止 Web 服务都不停止 Manager 中的实验。显式执行 `manager stop` 会等待当前作业结束；`manager stop --cancel` 取消并清理当前作业。意外中断需先核查自有进程、容器和外部请求，再使用 `operation acknowledge --acknowledge-cleanup` 解除派发阻塞；不会自动重放中断作业。批次恢复仍使用 `run --resume` 及冻结输入校验。详见 [Manager 生命周期](local-manager.md)。

默认不允许 live；模型配置与费用策略由用户本次操作明确选择，README 中旧实验授权不沿用。当前没有保证金额不超支的预算功能。

## 升级、备份与卸载

升级前停止新任务派发，等待当前作业结束；记录版本、未提交代码、实验目录和自有容器。不要用强制 reset/clean 覆盖自己的修改。退出使用该工作区的服务后，备份 `.workspace/`、`.console/`、`.rsi/`、`artifacts/`、实验配置、依赖锁、必要 Agent 源码/构建产物，以及用户级复评状态。`.workspace` 包括源码跟踪库、冻结配方和 Release；`.rsi/cycles` 包括闭环条件与逐轮证据。模型凭据另外安全备份，不放进 Git 或分享包。

SQLite 在所有相关写入进程停止后复制；不能仅复制运行中的主数据库文件而忽略 WAL。用户级复评状态可能被其他 checkout 使用，必须一并协调。详细路径、锁与回滚见 [迁移兼容基线](workspace-migration.md)。

拉取经过审阅的版本后执行 `uv sync --locked --python 3.12`，先 doctor 和 controls，再恢复服务。若旧实验代码清单不匹配，不绕过冻结校验；`workbench_repair` 只接受严格的基础设施修复清单。新功能不属于自动获准的恢复修复。

卸载先停止该工作区自有进程，按明确所有权清理实验容器。代码、可重建缓存和持久证据分别处理；保留备份再由用户选择删除哪些目录。禁止 `docker system prune` 或按模糊名称清理其他服务。回滚先停止新版所有写入方，再恢复匹配版本与备份；使用过 T03 导入时还需备份 `.workspace/`，旧程序不要读写新索引。导入不修改旧状态 schema。

来源 URL/ref、接入修复及有界合并后复评见 [源码跟踪](source-tracking.md)。原生平台 Agent 是可选能力，首次使用 Integration Agent/RSI 前需按 [平台执行器](platform-agents.md) 准备运行时和独立模型绑定。无需模型的常规配方构建和控制检查可先独立使用。实际测试范围与环境见 [完整验收记录](local-platform-validation.md)。
