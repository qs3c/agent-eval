# Agent Harness Eval

本地部署、单用户使用的 Agent Harness 实验工作台。调度、容器隔离和 verifier 复用 Harbor 0.24.0；各 Agent 保留原生循环。Agent 接入与 benchmark 相互独立。

## 从源码到接入验收

打开 **Agent 接入**，填写目标 Agent 的 GitHub 源码链接或平台主机 Git 目录、分支/ref，然后选择：

| 方式 | 使用方法 |
| --- | --- |
| 一键接入 | 选择 **OpenCode、DSH 或 Hermes**，选择接入模型并确认本次费用；平台排队读取源码、生成配方与 patch，再构建和验收 |
| 自行接入 | 点击“生成接入说明”，复制给自己的 Codex、Claude Code 等工具；它阅读平台与目标源码后生成配方和 patch，用户将产物放到平台主机，填写配方绝对路径并提交验收 |

两条路径遵循同一份[标准接入规范](docs/agent-integration.md)。平台接受标准交付物，不限制编写工具。接入工具与被测 Agent 是不同角色；用户自己的项目通过源码与配方导入，不作为公共内置选项分发。

自行接入不需要安装平台执行器或配置接入模型。外部工具自己的模型与费用由用户管理。生成说明不登记来源、不调用模型；提交配方才开始平台构建与验收。

一键接入首次使用时，先在下方内置 Agent 区准备指定 Docker 镜像并构建所选 Agent，再运行 `python scripts/prepare-rsi-runtime.py` 准备通用平台容器。在模型设置保存模型与凭据，服务启用 `--allow-live`。每次接入独立选择执行器和模型，任务固定 Release、模型策略、最多 50 次请求与 30 分钟时限；不依赖 RSI 页面的选择。详见[平台 Agent](docs/platform-agents.md)。

## 验收由谁负责

负责接入的 Agent 只生成候选，平台独立执行以下检查：

1. 校验配方字段、路径与来源；固定源码 SHA、配方和 patch。
2. Docker 隔离构建并记录不可变 Release 身份与产物哈希。
3. 执行至少两次完整隔离控制尝试，核验来源、正常结束的标准响应和原始证据。

**Runnable** 表示当前任务与环境下协议可运行，不要求题目得满分。**Diagnosable** 还要求模型请求及原生工具调用/结果证据。**Comparable** 当前未认证：成功运行不证明完整模型和网络覆盖。题目得分由 benchmark 的独立 verifier 判断。未知仓库不保证零配置成功；失败原因与证据保留供修复。

已登记来源可在“统一验收与源码跟踪”提交修复配方，或明确发起一次自动修复。普通更新须重新准入，历史实验不随源码变化；详见[源码跟踪](docs/source-tracking.md)。

## 本地安装与控制台

执行环境：Linux x86_64、Python 3.12、Git、uv 0.13.0 或更新版本、Linux Docker daemon。Windows 原生仅支持只读 doctor，不支持实验执行。首次安装与网络配置见[本地部署](docs/local-deployment.md)。

```bash
uv sync --locked --python 3.12
uv run agent-harness-eval doctor --json
uv run agent-harness-eval serve --port 8765
```

打开 <http://127.0.0.1:8765>。真实模型任务需另外以 `serve --allow-live` 启动并明确确认费用；不要将凭据写入配方、实验配置或 Git。模型池使用见[模型设置](docs/model-pool.md)。接入 Agent 的调用费用与被测实验分开管理。

浏览器关闭不影响独立 Manager。作业取消、恢复、资源协调和超时由平台负责，见 [Manager 生命周期](docs/local-manager.md)。可信私网访问使用明确的 `--host` IP；控制台没有应用层登录，不应公开到互联网。

## 内置开源基线

公共目录仅提供 **DSH、OpenCode、Hermes**。三者独立安装、固定版本、保留原生入口。评测基线和接入执行器可复用同一固定 Release，但执行角色、工作区、模型预算与证据各自独立。

DSH 使用锁定发布包及 Standard preset；源码 tag 是版本关联，不冒充从源码编译的产物。源码更新/RSI 候选须使用 `artifact_origin: source` 配方。各基线版本、构建条件与限制见[内置 Agent](docs/builtin-agents.md)。

## 评测、比较与改进

先接入自己的 Agent，再到“创建对比”选择 Release、benchmark、题目和模型。首次使用 `controls` 两题：模型 Agent 选 mock，无模型协议夹具选 offline。预检不发送模型请求。模拟结果只验证执行链路，不代表真实能力。

- [Benchmark 扩展](docs/benchmark-integration.md)：标准 Harbor 任务与 verifier 输出，与 Agent 适配独立。
- [模型策略](docs/model-policies.md)：模型参数、角色、预算与覆盖证据。
- 逐题对比页面：查看原生轨迹、补丁、成绩和缺失证据。
- [RSI 改进](docs/rsi.md)：冻结原始案例、诊断、独立候选、分层验证和双语草稿 PR。
- [清单与来源](docs/manifest-format.md)、[Trace](docs/trace-collection.md)：固定输入、保留原始事件与哈希。
- [有界批次](docs/batched-evaluation.md)、[实验守护](docs/experiment-guardian.md)：执行与资源控制。
- [迁移与备份](docs/workspace-migration.md)：旧 evidence 保持不变，`workspace-import` 默认预览。

SWE 补丁在 verifier 修改工作区前提取；导出保留全部尝试，官方独立复评单独记录，不把缺失评分算作成功。原始日志和模型请求是权威证据，ATIF 为可读派生视图。

## 目录与开发验证

`src/agent_harness_eval/integrations/` 包含配方、构建与接入检查；`platform_agents/` 管理公共执行器；`tasks/` 是独立控制任务；`experiments/` 是公共实验模板。用户 Release 与构建证据保存在 `.workspace/`，实验产物保存在 `artifacts/`；均不提交 Git。

```bash
uv run pytest -q
uv run agent-harness-eval run experiments/controls.json --output artifacts/controls-new
```

新增 Agent 优先提供标准 Recipe 和 CLI/Python 入口，复用通用适配器。高级扩展仍可实现 Harbor `BaseInstalledAgent` 并通过实验的 `implementation` 指定；它不会自动取得标准 Release 的验收等级。

本次改动的[验证记录](docs/onboarding-validation.md)与[公共／用户内容隔离说明](docs/public-workspace.md)分别记录实际证据、未验证范围和旧项目迁移边界。
