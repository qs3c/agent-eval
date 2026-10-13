# RSI 改进工作台

首版完成可重复执行的一轮闭环：测评证据 → 独立源码候选 → 配置检查 → 父版本/候选复评 → PR 发布与状态同步。候选档案保留父子关系、失败、持平和退化记录，后续可以选取已有候选继续改进。执行 Agent 内置 BKCrab 和 DSH 下拉选项，同时保留自定义适配器接口。

DGM 的参考点是利用执行反馈修改 Agent 自身实现、经验验证及保留多版本档案。原始实现见 [DGM](https://github.com/jennyzzt/dgm)、[父版本选择与档案循环](https://github.com/jennyzzt/dgm/blob/main/DGM_outer.py)、[改进步骤](https://github.com/jennyzzt/dgm/blob/main/self_improve_step.py)。本版是平台可操作的一轮流程与候选谱系，不宣称实现了 DGM 的自动种群采样和无限多代搜索。PR 发布是本平台增加的工程交付步骤。

## 使用界面

首页以诊断案例 / 候选版本档案为主，案例支持搜索题目与诊断主题。点击“新建诊断案例”打开表单；没有已结束实验时显示说明及实验记录入口。在“执行 Agent”卡片下拉选择 BKCrab 或 DSH，点击保存即可启用，无需编辑 JSON。模型名称、服务地址和 API Key 统一在左侧一级导航“模型设置”管理；RSI 卡片只选择模型池条目与执行时限。已有服务端 DeepSeek 配置自动迁入全局模型池，页面只返回凭据是否已配置。保存设置不会调用模型。案例详情可以直接创建关联候选。

证据索引按来源和完整性筛选，每页 20 项。人工快照、原始 Agent 轨迹和评分器输出分别标注；截断与缺失单独计数，长错误按需展开。候选详情分别展示代码检查、效果验证与交付状态，操作不可用时显示原因，补丁与 PR 正文折叠查看。

在升级后的控制台打开 **RSI 改进**。选择已结束实验、Agent、异常尝试或全部已记录尝试，点击“保存诊断材料”，生成固定的证据快照，无需填写改进目标。这一步不执行模型，也不需要已选择执行 Agent。

案例档案可独立打开，查看每份材料的来源、读取范围、截断和缺失；不必先创建候选。

创建候选时，执行 Agent 默认使用页面保存的选择，也可单次切换。执行 Agent 与被改进的 Agent 分开：例如选择 DSH，仍可改进案例对应的 BKCrab 仓库。模型与运行镜像固定到候选快照；后续修改设置不会改变已有候选的模型。队列复用现有控制台工作线程：优先处理评测队列，再执行 RSI 动作。重复提交同一候选的并发动作会被拒绝。

候选详情展示诊断引用、补丁、配置检查结果、复评结果、父提交和 PR 正文。**检查通过、观测改善、发布 PR 是不同状态**。运行异常的父版本不会被偷偷算作零分；缺失/异常、受辅助尝试或不一致的评测契约会阻止完整分数结论，可靠性变化另行显示。当前主分比较遵循平台既有 reward 越大越好的口径，不用于 loss 等越小越好的指标。

## 持久化与证据来源

所有 RSI 数据保存在项目 `.rsi/`，不写入历史实验：

- `cases/<id>/record.json` 与 `evidence/`：冻结案例、目标、实验身份、证据索引和字节副本。
- `candidates/<id>/record.json`：执行配置快照、父版本、提交、阶段、检查、复评和 PR 状态。
- `candidates/<id>/workspace/`：独立 Git clone，不共享原工作区文件和分支。
- `adapter-context.json`、`diagnosis.json`、`adapter-process.json`、`check-*.json`、`changes.patch`、`PR.md`：输入、模型判断、真实进程返回、候选补丁及交付正文。
- `evaluations/<id>/`：每轮复评的冻结任务、配置、父/子构建日志与产物哈希、两个独立 Harbor 报告和比较结论。
- `queue.sqlite`、`actions/`：原子领取的动作记录和 CLI 日志。

证据分类包括原始题目产物、Agent 轨迹、评分器输出、源码 provenance、守护事件、历史人工介入记录、历史人工诊断快照及实验级 watchdog 上下文。历史 `human-intervention.json` 中指向本实验 `interventions/` 的目录会被跟随采集；外部路径不跟随。**人工排查时补存的快照不是原生测评日志**，二者分别标记。不会在此步骤新查生产容器或补造已经销毁的现场。

每个文件记录源路径、原始大小、mtime、实际读取范围、导出字节哈希及截断/缺失。单文件最多取末尾 1 MiB，每案例最多导出 32 MiB / 512 个文件；这些是有限诊断材料，不是完整实验备份。大日志被截断时，Agent 必须保留不确定性。原始实验仍是完整来源。案例在运行前后检查索引、目标、实验身份和文件哈希。原始轨迹留在本地，API 不提供全文，界面只展示索引与诊断摘要；本地原日志可能包含敏感任务信息，沿用实验目录访问权限。

原仓库未提交改动不会带入候选，会显示提示。若 provenance 缺失、历史源码为 dirty snapshot 或提交不匹配，不声称候选父版本等同于历史被测实现；复评会重新构建当前固定的父版本。历史 dirty 源码若要精确重现，应先从已有源码归档恢复为明确的 Git 提交。

## 执行配置

内置执行器复用平台已经准备的 BKCrab 原生 binary 和 DSH Standard bundle，在独立 Docker 容器内运行。只读挂载冻结证据，读写挂载独立候选源码与输出，设置进程数、内存、CPU 与时限，并在结束/中断时清理容器。不会重启或修改线上 BKCrab 服务。运行镜像按 SHA 固定，凭据使用临时只读文件挂载，不放入命令参数或候选快照。

执行器和时限保存在 `.rsi/executor-settings.json`；模型元数据、场景分配与版本化密钥统一保存在忽略目录 `.console`。新任务冻结接口、模型、参数与凭据引用；修改或移除模型条目不改变已有候选。详细规则见 [全局模型池](model-pool.md)。

服务器运行包由部署流程准备：`python scripts/prepare-rsi-runtime.py --root <控制台目录> --artifacts-root <已有评测产物目录>`。它使用已有 `.cache/bkcrab-eval`、`.cache/dsh-bundle03/runtime.tar.gz` 构建共享镜像，并登记 `.cache/rsi-native.json`，无需终端用户操作。源码目标从服务器相邻的 `bkcrab` / `deepseek-harness` 仓库识别，仅使用固定 Git 提交。

内置 BKCrab 目标运行 `go test ./...`；DSH 目标当前只做补丁格式检查，页面明确标记这一限制。BKCrab 目标的父子复评自动从案例冻结证据恢复配置，适用于新候选与已有候选，也适用于由 DSH 执行改进的 BKCrab 候选，无需重新运行改进或填写模板。仅复测案例所选尝试对应的题目，沿用原评测模型、次数、时限和资源参数；守护统一为仅观察模式。原题目的内容哈希必须一致，缺失或变化时给出错误，不修改冻结证据。DSH 目标的源码构建仍需自定义 profile。没有复评结果时不会声称已验证模型效果。点击“创建并运行改进”才会启动执行 Agent 的付费模型调用。

内置父子构建在首次复评时固定服务器已准备的 Docker 镜像（未登记时使用原执行镜像），后续复评沿用该构建配置；原执行 Agent 的镜像和模型快照不变。各自编译对应提交的 `cmd/bkcrab-eval` 并生成源码与二进制 provenance，不复用历史测试二进制。构建阶段校验前端依赖锁文件并生成实际前端产物；缺少依赖或入口会明确构建失败。父子构建只生成产物，单元测试仍由原代码检查步骤负责，避免父版本的失败测试阻止基线复测。页面读取只检查可恢复配置，不启动构建、不修改候选。点击复评后记录派生模板、构建配置和每轮冻结输入，保留原执行配置。真实模型仍要求 `--allow-live` 与本次动作的费用确认；读取原模型的凭据引用，不替换成当前执行 Agent 的模型。

### 自定义执行器

参考 `rsi-profiles/bkcrab.json.example`，补充自己的适配器绝对路径、固定版本和 GitHub 目标后另存为 `.json` 才启用。示例没有指定任何模型，也不会自动成为可执行配置。BKCrab 的复评产物仍在 Harbor Docker 环境内运行；本版不部署或重启线上 BKCrab。

Profile 字段：

| 字段 | 含义 |
|---|---|
| repository / parent_ref | 本地目标仓库和父 ref，创建候选时解析成提交 SHA |
| adapter.argv / version / timeout_seconds / pass_env | 可信执行适配器、模型/提示词版本、进程期限、凭据环境变量名 |
| checks | 至少一个由管理员配置的验证命令；返回成功只代表这些检查通过 |
| build / artifacts | 分别构建父子版本，将 binary、bundle、provenance 映射到构建输出相对路径 |
| evaluation | 已准备的实验模板，必须包含案例 Agent；两个版本使用同一冻结任务集合 |
| github_repository / pr_base | 显式 PR 仓库 owner/name 和目标分支；必须匹配原仓库 origin |

命令不经隐式 shell 展开。支持参数内 `{python}`、`{workspace}`、`{output}`、`{evidence}` 替换；需要 shell 的配置必须明确使用 `sh -c`。凭据放入 `pass_env` 白名单对应的环境变量，不写进 argv。命令工作目录为源码副本，适配器路径使用绝对路径或已安装模块。测试/build 生成物应写到 `{output}` 或仓库忽略目录，不能在检查后修改已提交源码。通过 `git status` 和 HEAD 再检查保证 PR 对应已检查提交。

适配器、检查与构建程序是管理员安装的可信程序，运行于宿主机权限；独立 clone 不等于恶意代码沙箱。平台设进程超时、终止进程组、限制 stdout/stderr，但不能替任意脚本保证模型费用或清理它自行启动的远程任务/daemon。适配器应配置自己的模型用量限制。DGM 式预算化多代自动搜索尚未启用。

## Agent 协议 `eval-rsi/v1`

平台通过 stdin 写入一行 JSON，包含 `workspace`、`evidence_directory`、`objective`、`parent_commit`、`result_path`、适配器版本和 JSON Schema。适配器读取证据，直接编辑 workspace 的源码和回归测试，在 result_path 写入结果后以 0 退出。平台负责提交、验证和发布；Agent 不自行提交或操作原仓库。

```json
{
  "topic": "shell 超时后子进程未清理",
  "summary": "观察到的故障和诊断结论",
  "summary_en": "Observed failure and diagnosis",
  "evidence_refs": ["e0001", "e0007"],
  "hypotheses": ["尚待验证的机制解释"],
  "changes": "修改了什么以及加入了哪些回归覆盖",
  "changes_en": "Changes and regression coverage",
  "limitations": "缺失证据、未验证推断和适用范围",
  "limitations_en": "Missing evidence, unverified inferences and scope"
}
```

引用必须属于导出成功的证据项。结构合法不等于结论真实；验证与模型自述分开。Agent 的 stdout/stderr 作为有限本地日志保存。非零退出、超时、无修改、越权提交、无效引用或检查失败都保留为失败候选，不进入 PR 发布状态。

## 复评与 PR

改进档案的目标、诊断摘要、修改说明、假设及局限说明使用中文，技术标识和证据编号保留原文。新建案例不接收用户改进目标。执行 Agent 使用平台统一的诊断指令，在分析证据后生成必填的中文 `topic`（单行、不超过 120 字）；原因不明时明确标注“原因未确定”。诊断前卡片仅显示题目，诊断后显示最新一次有效诊断的主题；修复或检查失败不会丢弃已经验证的诊断结果。旧案例的冻结目标保留用于证据校验，不再展示或作为诊断指令。适配器结果必须同时提供 `summary_en`、`changes_en`；非空的 `limitations` 还需提供 `limitations_en`。PR 标题和正文按中文在前、英文在后的顺序生成，缺少英文翻译的历史诊断需补充后才能生成新的 PR 文案。

复评使用 build 配置分别从父/子 SHA 创建新副本，重新生成全部源码相关产物，覆盖实验模板里的旧 binary/bundle/provenance 路径；旧产物不存在也可以构建新产物。原有产物键没有全部覆盖时拒绝运行。产物哈希和所属提交写入记录，构建不能修改固定源码。

双方任务内容、模型、次数、时限、资源和守护策略必须一致。复评只允许无 Agent 辅助的观察模式守护；未配置时自动启用相同的观察/期限兜底。保留每道题结果、异常、输入/输出 token 与成本原始值。受辅助尝试不纳入“完整可比改善”判断。有限集合上的改善不代表统计显著性、隐藏集提升或泛化能力。

真实模型复评需要服务 `--allow-live` 且该动作勾选不设费用上限，与现有平台策略一致；一次动作运行父版本和候选两批。没有确认不执行。适配器自己的模型调用是另一个费用来源，不混入原 Agent 的 token/USD，未集成用量时视为未知。

点击“发布 PR”才会推送候选分支并调用已登录的 GitHub CLI。先验证源码未变化、目标 origin 匹配，再查询候选固定分支是否已有 PR；网络重试不会重复创建。PR 包含诊断、证据引用、父子提交、配置检查及复评结论；未经复评也可以发审阅草稿，但会明确标记性能提升未验证。已发布的 PR 会在复评结束后自动同步，也可点击“同步 PR”重试。不自动合并、部署或覆盖任何历史结果。

## CLI

```sh
uv run agent-harness-eval rsi-case artifacts/swe-live-20 --agent bkcrab \
  --trials django__django-15022__RkmihL7
uv run agent-harness-eval rsi-candidate CASE_ID --profile bkcrab
uv run agent-harness-eval rsi-action CANDIDATE_ID improve --execute
uv run agent-harness-eval rsi-action CANDIDATE_ID evaluate --execute
uv run agent-harness-eval rsi-action CANDIDATE_ID publish --execute
```

省略 `--execute` 只入队，由控制台或 `rsi-work ACTION_ID` 执行。真实模型复评额外需要 `--allow-live --confirm-unbounded`。基于已有候选继续改进用 `rsi-candidate CASE_ID --profile bkcrab --parent PARENT_CANDIDATE_ID`。失败候选不直接复用工作目录，创建新候选保留原始失败现场。

### PR 发布与复评同步

未复评的候选发布为草稿 PR。最新父子复评完成、明确 `improved`、没有比较问题或执行异常，且被测父子提交与候选记录一致时，发布为正式待审阅 PR；已有草稿在复评完成后自动同步正文并升级。持平、退步、失败或无法判断的结果不触发升级。自动升级不自动合并或部署，也不把人工转为正式的 PR 自动降回草稿。

发布与同步复用 `publish` 动作。已有 PR 的页面按钮显示“同步 PR”，可更新双语诊断、父子分数、比较结论与局限，并补处理历史候选。历史复评缺少显式提交字段时，使用冻结的父子构建产物元数据确认源码版本；无法确认时保留草稿。同步前检查 GitHub 的提交，提交不一致时拒绝修改；已关闭或已合并的 PR 只记录状态，不编辑或重新创建。

GitHub 同步错误单独保存在 `pr_sync_error`，不影响已完成的测评结果。页面显示重试提示，再次“同步 PR”即可恢复，既不重跑复评也不重复推送或创建 PR。展示的 PR 状态以最后同步时间为准；在 GitHub 手动修改后可再次同步刷新。CLI 同样使用 `rsi-action CANDIDATE_ID publish --execute`。

2026-10-13 验证：RSI/发布/Web/执行器/Console 回归 67 项通过、2 项环境条件跳过；前端交互 19 项通过；单独启用 `RSI_DOCKER_TESTS=1` 的真实 Docker 父子复评 1 项通过，确认父子提交与 0 → 1 的确定性评分。GitHub 更新与升级采用模拟命令覆盖，没有修改线上 PR。Ruff、JS 语法及 Git 空白检查通过。

服务正常停止会取消当前动作并清理进程组；异常中断记录在重启时变为 interrupted，不自动重放。独立 CLI 使用候选锁，活跃 CLI 不会被控制台恢复流程误标为已中断。整个宿主机失效或 SIGKILL 后仍需检查 Docker/外部程序遗留资源。CLI 和控制台执行目标为 Linux。

## 验证

2026-10-13 内置复评恢复验收：RSI 后端回归 70 项通过、2 项可选镜像测试跳过；前端交互 23 项通过；另行启用的真实 Docker 父子复评 1 项通过。覆盖旧候选恢复、Harbor 带命名空间的任务标识、原任务哈希校验、固定构建配置、模型凭据恢复与费用确认；未调用真实模型。服务器两个已有案例的任务副本均通过原始哈希校验。

前端交互回归使用 Node 内置测试运行器与 jsdom，依赖仅安装到忽略目录，不影响服务运行：

```powershell
npm install --prefix .cache/rsi-ui --no-audit --no-fund jsdom@29.1.1
$env:NODE_PATH = (Resolve-Path .cache/rsi-ui/node_modules).Path
node --test tests/rsi_ui.test.cjs
```

Linux 对应命令：`NODE_PATH="$PWD/.cache/rsi-ui/node_modules" node --test tests/rsi_ui.test.cjs`。覆盖搜索、证据筛选与分页、空尝试拦截、表单提交内容、创建成功但入队失败的恢复、零分展示、入队成功后刷新失败的重复操作防护。

2026-10-12 界面改版验收：7 项前端交互回归通过，Linux 上 12 项 RSI/Web API 回归通过。浏览器检查真实案例的档案搜索、来源筛选、空状态，以及 1440px / 390px 视口下的排版；窄屏文档宽度没有溢出，长表格在自身容器内滚动。新建表单和候选详情另外使用本地合成配置验收，未执行模型、发布 PR 或创建线上测试数据。截图保存在 `.cache/rsi-ui/`。

`uv run pytest -q` 覆盖本地 Git fixture、证据完整性、失败适配器、检查失败、谱系、队列、Web 边界和模拟 GitHub 重试。`RSI_DOCKER_TESTS=1 uv run pytest tests/test_rsi_docker.py -q` 使用真实 Harbor/Docker，分别构建父版本与修复候选，各评两道控制题，预期从 0 到 1；这是确定性平台验收，不是模型效果数据。

2026-10-12 已在服务器隔离目录 `/tmp/agent-harness-guardian-cgwNw3i9` 实际读取历史 `swe-live-20` 并创建两个案例：API 异常题 `django-14122` 导出约 5.37 MB；`/proc` 相关题 `django-15022` 导出约 7.27 MB，其中单独标记 6 份人工诊断快照。原始实验未修改，没有因此运行模型或给 BKCrab 提交修复。

同日验收：启用 `GUARDIAN_DOCKER_TESTS=1 RSI_DOCKER_TESTS=1` 的全量回归 113 项通过（144.42 秒）；随后补充活跃 CLI 锁与真实控制台队列执行覆盖，相关 RSI/Console/Web 共 44 项通过（3.92 秒）。真实配对复评确认父子各两道任务、源码提交及构建产物归属，控制题均分 0 → 1。新模块 Ruff、前端 JS 语法及 Git 空白检查通过。浏览器核验了两个案例的独立入口和人工快照来源；截图工具超时，因此没有完成截图层面的视觉验收。GitHub 发布使用模拟命令验证草稿、正文文件、固定提交和重复提交恢复，未创建真实 PR。
2026-10-12 执行 Agent 入口验收：10 项前端交互回归与 53 项 Linux RSI/Console/Web 检查通过，包含两种原生执行器的 Docker 离线启动检查。390px 窄屏配置表单无横向溢出。测试覆盖默认选择保存、凭据不回传、未知字段拒绝和执行器/目标仓库分离；未调用真实模型，模型任务的端到端效果尚未验证。
