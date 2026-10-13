# Agent Harness Eval

重点测试、比较和研究 Agent Harness 层差异的独立评测仓库。调度、容器隔离和 verifier 复用 Harbor 0.24.0；Agent 保留原生执行循环。Agent 适配器与 benchmark 任务相互独立。

## 本机 Web 控制台

```bash
export PATH="$PWD/.tools:$PATH"
uv sync --locked --python 3.12
.venv/bin/agent-harness-eval serve --port 8765
```

打开 <http://127.0.0.1:8765>。提供中文实验列表、模板创建、任务进度、执行日志和结果对比页面。默认本机单用户，FastAPI 同时提供页面与 API；原有 CLI 命令保持可用。

通过 Tailscale 直连时，使用 `agent-harness-eval serve --host 100.93.173.10 --port 8765`，打开 <http://100.93.173.10:8765>。`--host` 必须是具体 IP，默认仍为 `127.0.0.1`；Host 白名单仅增加指定地址，保留同源写入校验。控制台没有应用层登录，只应绑定可信私网地址。

- 页面可以选择模板中的 Agent、任务，并修改重复次数、并发、超时、CPU 和内存。提交时复制输入；实验按序执行，内部并发交给 Harbor。
- 控制台状态和输入保存在 `.console/`，原始输出仍保存在 `artifacts/web-<id>/`；均不提交 Git。每次提交产生独立 ID，不覆盖旧实验。
- 历史 `artifacts/` 只读登记。`completed` 表示结果完整且无运行异常，不表示全部任务得满分；缺失报告显示结果不完整。控制台读取的是 Harbor 内置评分，官方 SWE 复评仍用 `swe-regrade` 并单独核对产物。
- 配对比较检查 benchmark、任务内容哈希、模型配置、重复次数、资源和超时等条件。不兼容时展示差异，不输出配对结论；未知费用保持未知。
- 浏览器刷新不影响执行。服务关闭会终止自己启动的评测进程组；意外重启将未完成运行标为中断，不自动重跑。存在中断运行时暂停队列。检查对应输出中的 trial 名称和遗留进程、Docker 容器并完成清理后，用 `.venv/bin/agent-harness-eval serve --recover` 确认恢复；中断记录保留为失败，不重新执行。若旧进程仍持有锁，新服务拒绝启动。
- 默认禁止创建真实模型运行。需要时由调用环境注入 `DEEPSEEK_API_KEY`，使用 `serve --allow-live`，并在页面明确确认本次不设费用上限；历史授权不自动沿用到新任务。重启后执行旧队列仍检查本次启动策略。不要将密钥填入模板或页面。

首版不提供远程访问、账号系统、取消按钮或断点续跑。页面轮询读取文件，适合本地少量实验；数据增长后再引入结果索引。模板准备依赖原有安装步骤。验收见 [WEB_VALIDATION.md](WEB_VALIDATION.md)。

当前可以运行：确定性正负对照、两个原生 Agent 的模拟/真实 DeepSeek 接入、SWE 补丁导出和官方 harness 复评。真实试跑固定为 **DeepSeek V4.1 Flash (`deepseek-flash`)**；用户已授权本轮不设费用上限，仅运行 `django__django-13741` 一题，每个 Agent 一次。

## 结构

- `src/agent_harness_eval/agents/`：Harbor installed-agent 适配器；不包含 SWE 评分逻辑。
- `tasks/`：两个非 SWE 的文件任务，演示独立 benchmark 和多指标评分。
- `src/agent_harness_eval/benchmarks/`：SWE 任务加工、完整补丁提取、predictions 导出、官方复评入口。
- `experiments/`：Agent、任务、重复次数、资源与模型配置的组合。
- `locks/`、`uv.lock`：Python 与 DSH npm 依赖锁。
- `integrations/bkcrab-eval.patch`：BKCrab 仓库中独立入口的可移植补丁；当前工作区已添加该入口。
- `artifacts/<run>/`：冻结任务、Agent 包和来源、原始日志、ATIF、评分和报告。被 Git 忽略。

## 安装与无模型验收

环境：Linux x86_64、Docker、Go 1.25、Node 22、npm、uv 0.13.0。Python 主项目使用 3.12，SWE 生成器使用 3.13。当前机器的 uv 位于 `.tools/uv`。

```bash
export PATH="$PWD/.tools:$PATH"
uv sync --locked --python 3.12
.venv/bin/pytest -q
.venv/bin/agent-harness-eval run experiments/controls.json --output artifacts/controls-new
```

构建原生 Agent。若是全新 BKCrab checkout，先在该 checkout 中用 `git apply --check` 检查并应用本仓库 `integrations/bkcrab-eval.patch`；已有入口不要重复应用。

```bash
export BKCRAB_SOURCE=/home/xavier/Desktop/github/bkcrab
bash scripts/prepare-native.sh
.venv/bin/agent-harness-eval validate experiments/native-mock.json
.venv/bin/agent-harness-eval run experiments/native-mock.json --output artifacts/native-new
```

已有 `.cache/dsh-bundle03` 时，构建命令会拒绝覆盖；重建应选新的输出目录并更新实验引用。运行目录也拒绝覆盖。配置的相对路径以配置文件所在目录为基准。

`native-mock` 没有真实模型密钥；两个真实 Agent 连接容器内的确定性模拟服务，Agent 阶段禁止外网。模拟请求中的模型名只是协议测试标签，不代表已经选择或调用了这个真实模型。每个任务创建全新 Agent home 和工作区。

## DSH Standard 到底是什么

固定 npm `@deepseek-ai/dsh@0.2.1-alpha.2`，原样打包 `dsh-web-app/presets/standard.patch.yml`。SDK 仅作为无界面启动载体；实际通过官方 `agentPresets.mount(..., 'standard')` 加载 Standard。不会拿 SDK 默认工具集合或 `sdk-minimal` 作为 Standard。

启动前校验 bundle 哈希、preset、必需模块和宿主服务；运行时由官方 registry 检查组合，并保存实际组合与展开配置。已验收 26 个活跃模块，包含 persona、文件/shell、instructions、compaction、tool-result-pruner、todo、subagent 等。原始 preset SHA、依赖锁、Node SHA 和适配器 SHA 均被记录。

差异显式保留：无浏览器 UI，容器内使用 `danger-full-access`，禁用 office/runtime 额外包，干净用户状态，无外部个人 skills。子代理、压缩等模块已加载；短任务没有触发它们的完整行为，不能据此宣称所有功能均已验证。

BKCrab 使用 `agent.NewManager` 和原生 `HandleMessage`，文件工具与 shell 映射同一任务目录。评测配置不接入网关、IM、RAG、MCP、跨任务记忆、技能学习及数据库 goals/cron；这些差异保存在 `run_config`。这次比较的目标是隔离的 coding profile。

## 扩展 Agent 与 benchmark

新增 Agent：实现 Harbor `BaseInstalledAgent`，声明 `supported_model_modes`；需要统一模型配置时设置 `accepts_model_config = True`，接收 `model_config` / `timeout_seconds`。也可继承本仓库 `Native`。在实验中设置 `implementation: "your_package.module:YourAgent"`、版本与能力即可，无需改核心分发或 benchmark。

新增 benchmark：产生标准 Harbor 目录 `instruction.md`、`task.toml`、`environment/`、`tests/test.sh`，可选 `solution/`；verifier 输出 `/logs/verifier/reward.json` 或 `reward.txt`。实验选择任务列表、所需能力和主指标即可，无需修改 Agent。SWE 才使用补丁导出；一般任务可以返回任意数值指标。

能力声明只是接入前的兼容检查，真正行为仍需各自 smoke 验收。模拟模式要求任务声明 `agent.network_mode = "no-network"`；新增第三方 Agent 的真实模型接入仍需单独实现和验证。

## SWE 首期流程

```bash
bash scripts/prepare-swe.sh django__django-13741
.venv/bin/agent-harness-eval run experiments/swe-controls.json --output artifacts/swe-new
.venv/bin/agent-harness-eval run experiments/swe-native-mock.json --output artifacts/swe-native-new
.venv/bin/agent-harness-eval swe-export artifacts/swe-new artifacts/swe-new-predictions
.venv/bin/agent-harness-eval swe-regrade artifacts/swe-new-predictions/oracle.attempt-1.jsonl \
  --dataset datasets/swe-smoke/records.json --run-id oracle-control
# 审阅命令后加 --execute 执行。没有真实模型调用；会构建/运行 Docker 环境。
```

使用固定 Harbor 源码生成任务，再固定 Docker digest 和 uv；在准备阶段打包 CPython 3.11 及评分依赖，记录实际包版本和文件哈希，评分阶段不再临时安装依赖。数据记录保存在本地 `records.json`；每轮另存 task 内容哈希。参考补丁、隐藏测试和预生成的 TestSpec 只在 solution/verifier 阶段挂载，不放入 Agent prompt。评分器缺少有效 report 时产生评分异常，而不写成零分。

补丁在 verifier 开始、隐藏测试被应用或重置前提取。临时 Git index 相对 `base_commit` 捕获已提交、暂存、未暂存、未跟踪文件以及删除，不修改 Agent 原 index。每个 Agent/attempt 导出独立 predictions，禁止 best-of 挑选；没跑完或没提取到的补丁以空补丁计入，并在 export manifest 标注原因。

Harbor 上游模板内置 SWE parser **4.0.3**；官方独立复评固定 **4.1.0**。内置 parser 的 `datasets==2.16.1` 额外约束 `pyarrow==20.0.0`。两种评分必须分别保留，不能默认为等价。`swe-regrade` 使用官方 harness 在干净容器重新应用补丁。正式评测以独立复评结果为准。

## 真实单题试跑

初次准备先运行 `bash scripts/prepare-swe-live.sh`，从已固化的离线 SWE 任务创建只允许 DeepSeek 出网的副本；已存在时拒绝覆盖。

`experiments/swe-live-one.json` 固定一题、两 Agent、各一次、串行、每边 1800 秒。`model.spending="unbounded"` 是用户明确授权的费用策略，不是费用限额；超时只是防止挂起。暂不提供有限金额预算模式。

```bash
# .env 为本地 0600 文件，被 Git 忽略；也可以由调用环境直接注入密钥。
set -a
source .env
set +a
.venv/bin/agent-harness-eval run experiments/swe-live-one.json --output artifacts/swe-live-new
```

容器内 relay 仅转发官方 `api.deepseek.com`，分别使用 OpenAI Chat Completions / Anthropic Messages 协议；模型名、max_tokens=32768、thinking=enabled、effort=high、temperature=1、top_p=0.95 统一。Agent 原生循环、工具、系统提示和上下文管理仍各自保留。`native_wire_requests.jsonl` 保留归一化前请求，`wire_requests.jsonl` 为实际发送请求；流式事件、错误、usage 单独保存。

真实密钥通过临时文件传入 relay，读取后删除；Agent 使用占位凭据。日志不保存认证请求头，manifest 只保存密钥环境变量名。模型 usage 累加官方响应；缺失时记 null。费用尚未按价格表估算，账单金额以 DeepSeek 平台为准。运行后必须分别导出两边补丁并完成官方复评；单题结果只用于接通链路，不能形成总体排名。

## 证据与边界

每轮保存 manifest、冻结任务/Agent 文件和 SHA、Harbor 配置、原生日志、原始模型请求、覆盖说明和报告。ATIF 是末次请求上下文加终态的可读视图，**不是无损全轨迹**；原始事件和请求为权威证据。usage 缺失记 null，模拟计数仅用于接入测试；模拟实际 API 费用为 0。

报告保留运行异常、缺失 attempt、原始指标和分母，同时给出已评分均值及未评分按零计入的值。逐任务配对要求所有 attempt 都评分完成。不同 benchmark 不合成总分，小样本不输出显著性结论。

新的 manifest v2 将文件清单分离为去重附件，主清单只保存配置、摘要和引用。新准备的 SWE 任务使用公共评分运行时归档；历史实验可运行 `agent-harness-eval manifest-index artifacts/<run>` 生成小索引，原清单不覆盖。审计使用 `manifest-verify`；详见 [清单格式](docs/manifest-format.md)。

新的 BKCrab 源码快照以 Git commit/tree 标识已提交内容，并保存未提交差异和未跟踪文件；旧快照只有来源哈希，不能代替源码归档，历史实验不会自动补造证据。无模型验收见 `VALIDATION.md`，真实单题试跑与官方复评见 `LIVE_VALIDATION.md`。

控制台默认展示已复核的真实评测；平台自测和调试记录可单独筛选。同一批次可能同时包含有效和诊断 Agent，批次完成次数不等于通过题目数。

“结果对比”可选两条真实任务轨迹，查看请求/工具次数、缓存输入、测试返回、最终补丁与逐轮证据；原有汇总比较保留在“批次汇总对比”。历史样本的解释性注释保存在 `console_reviews.json`，只有证据哈希仍匹配时展示为已复核；新实验不会自动继承结论。缺少响应或计时证据时显示未知，不生成推测性的耗时归因。

Trace 的原生埋点、代理采集、v2 时间口径与历史兼容说明见 [Trace 采集文档](docs/trace-collection.md)。新的代理计时随下次真实运行产生；已有实验不会补造历史时间。

实验运行守护支持定时监测、介入前取证、可插拔 Agent 直接处置和独立超时兜底。配置、JSONL 接口、证据来源及无人值守边界见 [实验守护文档](docs/experiment-guardian.md)。首个 Agent 尚未指定；`experiments/guardian-controls.json` 提供无需模型的观察模式验收入口。

大规模实验支持按批准备镜像、同批完成全部 Agent 和官方复评后清理，并限量保留异常工作区。配置、证据与恢复方式见 [有界批次测评](docs/batched-evaluation.md)。

RSI 改进工作台连接诊断证据、可插拔 Agent、独立源码候选、检查、父子版本复评和草稿 PR，保留候选谱系与失败记录。控制台入口为“RSI 改进”；配置与使用见 [RSI 文档](docs/rsi.md)。未配置执行 Agent 时可先冻结案例，默认不会调用模型或发布 PR。

## 实验内 Agent 对比与版本

打开 `/#compare`，先选择一个实验，查看本次全部任务的各 Agent 总览，再在逐题矩阵中选择两个 Agent。点击任务可并排展开补丁、测试输出和原生轨迹；重复尝试有独立选择器。运行详情中的「对比本实验 Agent」会直接进入该实验。只有一个 Agent 的实验也能查看成绩与单条轨迹。

SWE 主分使用 `resolved` 语义：每题先计算所配置尝试的成功比例，再对任务等权平均。一次尝试就是本次子集解决率；多次尝试不会偷偷取最优。未结束、缺失、异常、无有效评分或额外记录均有独立计数，完整评分前只显示「已观测成功次数 / 计划次数」的暂定比例，不把未评分解释为失败。非二值 benchmark 保留其原始主指标单位。整体表始终覆盖本次全部任务，不随下方筛选改变。

输入/输出 Token、执行秒数、USD 费用来自运行器原始结果。完整覆盖计划尝试时才显示总量，否则显示未知以及已知部分和覆盖次数；轨迹页另行核对原始请求完整性。诊断记录和待复核结果不会自动变成有效评测。Harbor 内置评分与官方独立复评仍分别标注。

新实验可以在模板 `benchmark` 中声明以下信息，和选题清单一起冻结到实验配置及 manifest：

```json
{
  "id": "swe-verified-sample",
  "tasks": ["../datasets/swe-live-one/django__django-13741"],
  "primary_metric": "reward",
  "score_kind": "resolved",
  "dataset": {
    "name": "princeton-nlp/SWE-bench_Verified",
    "revision": null,
    "split": "test"
  },
  "evaluation": null
}
```

`dataset.revision` 应填写实际获取数据时固定的修订；`evaluation` 可记录 `name`、`version`、`environment`（例如实际评分器版本、环境镜像摘要）。这些是可追溯的配置声明，控制台不会据此下载/切换数据，也不会验证声明的远程版本。需要先准备对应版本的真实任务目录，再创建实验。现有 SWE 模板记录了已知的数据集来源与 test split，未保存的修订仍为 null；旧实验绝不使用新模板追填版本。旧 SWE 的 resolved 语义仅从冻结的 SWE 任务证据识别，不覆盖显式主指标声明。

跨实验批次比较保留为辅助入口。它检查数据集名称/修订/split、评分器/环境声明、任务内容哈希、模型、主指标、重复次数与资源等条件；旧 SWE 的数据集版本不明时不声称可直接配对。不同版本和不同选题的实验不会自动合并成总分。`GET /api/runs/{run_id}/comparison` 提供只读的整体统计和逐题结果；不会启动评测。
