# Benchmark 生命周期与选择

`benchmark list` 列出 `controls`、`harbor-tasks`、`swe`。实验的 `benchmark.plugin` 可以显式选择；旧配置依据已知 SWE 数据集名称、SWE 来源记录或明确的 SWE 批次选项兼容解析。仅将 `score_kind` 设为 `resolved` 不会选择 SWE。

插件使用相同的 resolve/materialize/prepare/collect/grade/release 边界。resolve 固定任务顺序、内容 hash、数据集与评分器身份；prepare 返回任务及资源依赖，批次调度继续使用主机预算与镜像消费者保护；collect 保留每一次尝试；grade 与执行分离；release 使用现有的自有资源校验，不删除外部镜像引用。

SWE 插件沿用锁定版本的任务准备、补丁捕获、全部重复尝试导出和官方复评。官方评分失败不会转用 Harbor 分数冒充最终结果。数据准备仍使用已有 `scripts/prepare-swe.sh` 等显式入口，插件注册不代表数据或镜像已安装。数据集 revision 缺失时仍显示未知，不把读取当天的 HEAD 当作已冻结版本。

自定义 benchmark 首期支持标准 Harbor 文件任务：`instruction.md`、`task.toml`、`environment/` 和独立 `tests/`。Agent 不应读取 `tests/` 或参考 `solution/`；容器阶段隔离由 Harbor 执行，真实协议验收检查这些文件在 Agent 阶段不可见。任务作者不能自行把隐藏答案 COPY 到环境镜像或写进提示；平台不能从任意自定义代码中识别所有语义泄漏。

当前运行时只承诺 filesystem/shell 能力；需要模拟用户、持续多步骤交互等能力的任务在预检时拒绝，不靠 Agent 自报 capabilities 放行。

## 冻结子集

```bash
agent-harness-eval benchmark inspect --config experiments/controls.json
agent-harness-eval benchmark select --config experiments/controls.json \
  --count 1 --seed 17 --output /tmp/controls-subset
agent-harness-eval run /tmp/controls-subset/experiment.json --output artifacts/subset-run
```

需要排除以前选过的题目时，添加一个或多个 `--exclude-run <实验目录>`。排除记录必须属于相同 benchmark；排除集合按任务 ID 处理。候选数不足则报错，不偷偷减少题量或重复采样。

抽样算法为 `sha256-seed-task-rank-v1`：按 seed 与任务 ID 的哈希排序后取前 N 项，不依赖文件枚举顺序或 Python 随机数实现。`selection.json` 保存 dataset/revision/split、seed、算法、排除集合、候选内容 hash 和最终有序 task_ids。所有候选内容变化都会改变选择身份；冻结过程再次检查内容，避免选择后复制到不同版本。

CLI/Web 提交实验时继续复制选择记录并核对其 hash；历史结果不因源目录后续变化而改变。结果保留全部重复尝试，不做 best-of 筛选。
