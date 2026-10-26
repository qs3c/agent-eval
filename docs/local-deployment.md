# 本地单用户部署与环境诊断

本页描述 P0 已提供的入口。统一后台 Manager、Web 暂停/取消、通用 GitHub 接入和自动升级闭环仍在后续阶段，不应按目标架构假定已经实现。

## 支持范围

| 场景 | 当前要求 |
| --- | --- |
| 平台执行 | Linux x86_64、Python 3.12、可访问的 Linux Docker daemon、Git、uv 0.13.0 或更新版本 |
| controls | 只需平台依赖；不需要 API key、Go、Node 或 BKCrab checkout |
| DSH 构建 | 额外需要 Node 22 或更新版本、npm、锁文件；不需要 Go 或 BKCrab |
| BKCrab 构建 | 额外需要 Go 1.25 或更新版本、含 eval 入口的 BKCrab checkout；不需要 Node/npm |
| SWE 准备 | 额外需要 uv 管理的 Python 3.13、数据与镜像下载空间；评分运行时另按固定构建准备 |
| Windows 原生 | 可运行只依赖标准库的 doctor 获取诊断；不支持执行实验 |
| WSL2 | 能检测并提示；尚未完成支持认证，不能用 doctor 通过代替完整验收 |

版本下限只用于预检；具体可复现运行仍使用 `uv.lock` 和 Agent 锁文件。CPU/内存诊断是环境信息，不是并发准入。默认检查 20 GiB 空闲余量，SWE 实际占用可能远大于此值。doctor 不安装组件、不拉镜像、不运行 Agent、不发送 API 请求、不启动/停止进程。

## 安装与无模型验收

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

BKCrab checkout 必须已经具有 `cmd/bkcrab-eval`。首次应用平台补丁前先执行 `git apply --check`，已具入口不要重复应用。独立构建不等于自动生成单 Agent 实验：使用现有模板时仍需只选择已准备的 Agent，`native-mock.json` 默认含两个 Agent。DSH 已存在的 bundle 继续拒绝覆盖，重建请选择新的产物位置并修改实验引用；通用 Release 管理属于 P2。

平台主程序目前使用 Python 环境启动；Agent/任务执行使用 Docker。部署 BKCrab 服务到项目约定服务器时，仍须 Docker 部署并遵守既有端口与源码流程；本页不要求为运行 controls 停止 BKCrab 服务。

## Web、生命周期与私网

默认访问 `http://127.0.0.1:8765`。私网连接使用 `serve --host <可信私网IP> --port 8765`，必须给具体地址，保留 Host 白名单与同源检查。当前控制台没有账号登录，不公开暴露。

浏览器刷新不影响实验；当前 Web 服务关闭会终止其启动的子进程。意外重启后的队列恢复仍需检查遗留的自有进程/容器，再按现有 `serve --recover` 语义恢复派发。不要将它当作恢复全部已中断实验；批次恢复使用原 `run --resume` 及冻结输入校验。独立 Manager 属于 P1。

默认不允许 live；模型配置与费用策略由用户本次操作明确选择，README 中旧实验授权不沿用。当前没有保证金额不超支的预算功能。

## 升级、备份与卸载

升级前停止新任务派发，等待当前作业结束；记录版本、未提交代码、实验目录和自有容器。不要用强制 reset/clean 覆盖自己的修改。退出使用该工作区的服务后，备份 `.console/`、`.rsi/`、`artifacts/`、实验配置、依赖锁、必要 Agent 源码/构建产物，以及用户级复评状态。模型凭据另外安全备份，不放进 Git 或分享包。

SQLite 在所有相关写入进程停止后复制；不能仅复制运行中的主数据库文件而忽略 WAL。用户级复评状态可能被其他 checkout 使用，必须一并协调。详细路径、锁与回滚见 [迁移兼容基线](workspace-migration.md)。

拉取经过审阅的版本后执行 `uv sync --locked --python 3.12`，先 doctor 和 controls，再恢复服务。若旧实验代码清单不匹配，不绕过冻结校验；`workbench_repair` 只接受严格的基础设施修复清单。新功能不属于自动获准的恢复修复。

卸载先停止该工作区自有进程，按明确所有权清理实验容器。代码、可重建缓存和持久证据分别处理；保留备份再由用户选择删除哪些目录。禁止 `docker system prune` 或按模糊名称清理其他服务。回滚先停止新版所有写入方，再恢复匹配版本与备份；P0 没有新状态库迁移，不修改历史 schema。

实际测试范围与环境见 [P0 验证记录](p0-validation.md)。
