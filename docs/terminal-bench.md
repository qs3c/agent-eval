# Terminal-Bench 接入

工作台通过 Harbor 官方 registry 获取任务，保存上游 Git commit / 包内容哈希、任务文件 SHA-256、抽样 seed 和筛选记录。原始 `instruction.md`、`task.toml`、solution 和 verifier 原样复制，不使用 SWE 插桩或 SWE 复评。沿用已有 Agent 原生执行循环、模型代理、实验报告与逐题证据。

## 准备四 Agent 实验

准备阶段只下载与验证输入，不调用模型、不创建控制台运行。

```bash
.venv/bin/agent-harness-eval terminal-bench-prepare \
  --dataset terminal-bench/terminal-bench@4.0.0 \
  --destination datasets/terminal-bench-4.0.0-cpu30 \
  --template experiments/swe-four-agent-new-30-20261017.json \
  --config-output experiments/terminal-bench-four-agent-cpu30.json \
  --count 30 --cpu-only --seed 20261018
```

以上目录已经准备过时，命令拒绝覆盖；再次准备请使用新的目录和配置路径。`--task NAME` 可指定题目，多题重复传参；不能与 `--count` 混用。不传任务筛选则准备全部任务。

支持固定版本的旧 registry 引用 `terminal-bench@2.0` 和包 registry 引用 `terminal-bench/terminal-bench@4.0.0`。禁止 `latest/head`。4.0 包版本解析为具体数据集内容哈希，每道题也固定为内容哈希；2.0 每道题固定为 Git commit。

本地工作台目前使用 Linux Docker CPU。默认遇到需要 GPU/TPU 的 Agent 或 verifier 环境会报错；只有显式指定 `--cpu-only` 才排除这些任务。先筛选 CPU 任务，再按 seed 抽样，确保 `--count 30` 真正得到 30 题。筛选后数量不足会报错。CPU 子集不是完整 Terminal-Bench，不能直接与完整榜单分数比较。

## 配置与运行

生成的配置可以用现有命令验证与运行：

```bash
.venv/bin/agent-harness-eval validate experiments/terminal-bench-four-agent-cpu30.json
# 正式付费运行时执行；需要相应模型凭据和费用授权：
.venv/bin/agent-harness-eval run experiments/terminal-bench-four-agent-cpu30.json \
  --output artifacts/terminal-bench-four-agent-cpu30
```

保存到 `experiments/` 的 JSON 也可被控制台模板列表发现；运行中的旧服务需加载新代码后才能识别新的可空资源配置。控制台可以选择四 Agent 中的一部分或任务子集。

生成器复用模板的 Agent 构建、模型和重复次数，并做以下明确调整：

- `cpus`、`memory_mb`、`timeout_seconds` 为 `null`，使 Harbor 使用每道题的官方资源和 Agent 超时。已有 SWE 配置的默认值仍是 2 CPU、4096 MiB、300 秒。控制台这些字段留空表示使用题目配置，也可以填写显式覆盖。
- `batch.prepare_swe` 与 `batch.official_regrade` 关闭；不产生 SWE 补丁或宣称有 SWE 官方复评。
- 守护改为 `observe`，不沿用自主修复介入。SWE 专用搜索审查关闭，避免未经验证的跨基准政策影响任务；这不证明不存在答案泄漏。
- 独立 verifier 保留上游隔离方式，使用 Harbor 原生 Job；此时关闭工作台 Guardian 和批次，因当前 Guardian 只支持共享 verifier。这样的配置暂不支持工作台 `--resume`。
- 多阶段任务、非 Linux 任务，以及包含未接入的数据集级评分文件的版本，提前报不支持，禁止静默改变评分含义。

任务下载记录位于 `datasets/<selection>/selection.json`；运行时该记录连同任务被再次冻结并写入 manifest。CLI 和控制台两条路径均保留选择记录。任务文件固定不代表外部镜像标签、apt/pip 下载依赖固定；4.0 任务若自带镜像 digest，则原样保留。

## 零模型费用验收

本次使用官方 Terminal-Bench 2.0 的 `fix-git` 与 `openssl-selfsigned-cert` 做基础设施控制验证。控制 Agent 为 Harbor 的 `oracle`（执行上游参考解）和 `nop`（不做题）。这些结果不是 BKCrab、DSH、OpenCode、Hermes 的能力成绩。

实际结果：4/4 次尝试完成，无执行异常；oracle 两题 reward 均为 1，nop 两题均为 0；运行 manifest 校验通过。相关 Python 回归测试 96 项通过，浏览器测试 7 项通过。正式四 Agent 模型评测未启动。

已准备 4.0.0 的 CPU 30 题配置，源数据集共 66 题；显式排除 `fp8-rmsnorm-gemm`、`jax-speedrun-gpu`、`math-eval-grader` 后抽样。数据集内容哈希为 `sha256:39d9f44b40420cde8fdcc087579c0d72a7e14fa3656d603c3f0d22fb35e27732`。本次记录的 30 题均使用官方 28800 秒 Agent 上限；未自行缩短为 SWE 的时限。

接入验收结束时，另一会话已启动 `swe-four-agent-new-30-20261017`，因此控制台重载在活动运行检查处取消，未中断该实验。待它结束后重载：`systemctl --user restart agent-harness-eval-console.service`，再刷新控制台模板列表。

本地输入和结果：

- 控制模板：`experiments/terminal-bench-controls.json`
- 官方任务：`datasets/terminal-bench-2.0-smoke/`
- 验收运行：`artifacts/terminal-bench-controls-20261018/`
- 4.0 包格式真实导入验证：`datasets/terminal-bench-4.0.0-smoke/`，题目 `layout-config-recreation2`；仅验证下载、原始文件和配置，未执行该题。

重新获取控制输入时，可先从仓库自带 fixture 创建独立的 oracle/nop 模板（无需已有 Terminal-Bench 数据）：

```bash
.venv/bin/python - <<'PY'
import json
from pathlib import Path
config = json.loads(Path('experiments/controls.json').read_text())
config['agents'] = [
    {'id': 'oracle', 'implementation': 'oracle', 'version': 'harbor-0.24.0'},
    {'id': 'nop', 'implementation': 'nop', 'version': 'harbor-0.24.0'},
]
Path('.cache').mkdir(exist_ok=True)
Path('.cache/terminal-control-template.json').write_text(json.dumps(config))
PY
.venv/bin/agent-harness-eval terminal-bench-prepare \
  --dataset terminal-bench@2.0 \
  --template .cache/terminal-control-template.json \
  --destination datasets/terminal-bench-2.0-controls-next \
  --config-output experiments/terminal-bench-controls-next.json \
  --task fix-git --task openssl-selfsigned-cert
```

运行测试：

```bash
.venv/bin/pytest -q tests/test_terminal_bench.py
NODE_PATH=/tmp/agent-eval-regrade-ui-tests/node_modules \
  node --test tests/terminal_bench_ui.test.cjs
```

浏览器测试需要 `jsdom`，沿用本机已有测试运行时，不增加产品依赖。

官方来源：[运行说明](https://www.tbench.ai/run)、[Harbor 任务与数据集](https://docs.harborframework.com/)、[Terminal-Bench 2.0 任务仓库](https://github.com/laude-institute/terminal-bench-2)。
