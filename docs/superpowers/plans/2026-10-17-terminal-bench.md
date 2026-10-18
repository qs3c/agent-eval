# Terminal-Bench 接入实现计划

**目标：** 将固定版本的官方 Harbor Terminal-Bench 任务导入现有工作台，沿用 Agent、批次、冻结和报告机制；本次不启动付费模型。

**设计：** 新增独立 benchmark 模块和 `terminal-bench-prepare` 命令。通过 Harbor registry API 获取固定任务版本，复制任务原文并保存上游标识与文件哈希，生成可被现有控制台读取的实验配置。任务自带的 CPU、内存、Agent 超时优先，实验的这些覆盖参数允许显式为 null。SWE 准备、插桩和复评不用于本基准。

**技术：** Python 3.12、Harbor 0.24.0、Pydantic、现有 Docker runtime。

**约束：** 保留其他会话在主目录的修改；只新增 Terminal-Bench 相关文件并对共享配置和 CLI 做小范围增量修改。版本必须明确固定；默认不筛除 GPU；本地 Docker 不支持的任务提前报错。CPU 子集必须显式选择并记录；不将子集分数称为完整榜单成绩。异常不伪装成 Agent 失败。

- [x] 实现 registry 下载、确定性抽样、原始文件冻结、来源和子集记录；通过本地 registry fixture 验证版本锁与下载接口。
- [x] 实现从已有实验模板生成 Terminal-Bench 配置，关闭 SWE 操作与自主介入，保留任务资源和超时；接入 CLI 和控制台模板目录。
- [x] 测试官方任务不被改写、资源保留、GPU 与空集合拒绝、可重复抽样、临时目录验证后落盘与拒绝覆盖。
- [x] 下载真实固定版本官方任务，运行零模型费用 oracle/nop 控制验收，核对结果、report 和 manifest。
- [x] 添加中文使用文档，记录实际版本、验证结果与限制。

实际验证：Python 相关回归 96 项通过，浏览器 7 项通过，ruff 和 diff 检查通过。2.0 两题 oracle/nop 共 4 次尝试，无异常，reward 分别为 1/1 与 0/0，manifest 校验通过。4.0.0 真实包导入通过，并已生成四 Agent CPU 30 题输入，未调用正式模型。

运行环境限制：4.0 的独立 verifier 使用 Harbor 原生 Job，暂不接工作台 Guardian/batch/resume；多阶段任务明确拒绝。当前服务的重载被活动 SWE 实验检查阻止，未执行重启，待该实验结束后加载新代码。
