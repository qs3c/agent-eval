# 镜像清理根因修复与实验恢复计划

**目标：** 修复历史运行镜像标签识别，合并服务器 uv 路径修复，保留待复评镜像和原始证据，部署后补评 6 项并恢复剩余 27 题。

**设计：** 在现有批次清理中，用当前批次结果、guardian 捕获的镜像 ID、Compose project 和 trial 名共同确认运行镜像归属。只删除精确匹配的 Compose `project-main:latest` 标签，保留旧 `hb__` 兼容性、外部标签、容器引用和全局租约锁。历史清理独立记录审核清单和执行结果，不覆盖原有 cleanup.json。

**约束：** 不删除实验目录、日志、补丁、报告、冻结输入、数据卷或生产容器。不释放未确认结束的租约；不启动新的实验替代原实验。服务器未提交修改先备份再部署。

- [x] 将服务器 swe.py 和 uv 测试带回本地，补充 README。
- [x] 在 tests/test_image_cleanup.py 覆盖真实 Compose 命名、外部标签、错误 project、其他任务、容器引用和删除失败；旧实现 3 failed、4 passed，修复后通过。
- [x] 修改 batching.py 的精确归属识别，相关回归 57 passed；全量 344 passed、11 skipped；其后新增恢复集成用例所在模块 8 passed；真实 Docker 隔离验收 1 passed。
- [x] 生成历史候选清单：119 个源自 swe-live-80、官方评分完整、没有其他引用的镜像；其余镜像和全部租约保留。
- [x] 提交推送，备份服务器 uv 差异并快进部署至 `2bb56fc`，重启评测控制台；网页和接口 HTTP 200，服务实际环境中的 uv 解析和执行正常。
- [x] 在互斥锁内重新验证清单后逐标签删除 119 个历史镜像；17,620 个原始实验文件哈希一致，保留镜像和租约不变。另清理超过 24 小时未使用的构建缓存，磁盘余量由约 25.23 GiB 增至 130.94 GiB。
- [x] 核实恢复余量，提交原实验第一批补评；6 项均产生有效报告，两种 Agent 各 3 项 resolved=1、errors=0。用原冻结配置 --resume 启动剩余 27 题，已进入第二批准备；不代表剩余题目已完成。

验证命令：在服务器独立临时目录使用现有 Linux Python 环境运行 `python -m pytest tests/test_image_cleanup.py tests/test_uv_resolution.py tests/test_batching.py tests/test_image_lifecycle.py tests/test_regrading.py tests/test_swe.py tests/test_web.py -q`；随后执行全量测试。真实 Docker 验收只使用隔离小镜像和专用测试标签。

恢复约束补充：原实验的工作台文件清单与提交 `20ae8e7` 完全一致，当前主线已含后续搜索评审变化。恢复从 `20ae8e7` 构建独立运行目录，仅叠加本次四个基础设施文件的修复；使用 `--workbench-repair` 校验完整新旧哈希映射并追加修复记录，不改写原始冻结代码清单。测试覆盖未授权变化拒绝、Agent 变化拒绝、清单不匹配拒绝、原始清单保持不变及冻结任务校验继续生效。

运维审计保存在服务器 `.cache/image-cleanup-recovery-20261016/`：`plan.json`、`execution.json`、`cache-cleanup.json`、前后证据哈希清单、服务器部署前补丁、修复清单与独立运行代码。三条重复准备租约确认原进程已退出、没有尝试目录或结果、任务已由目标实验首批接替后退役；原租约和状态另存 `retired-preparation-leases.json`，当前实验租约继续保护。目标运行 ID 为 `7935e1eb71404d80919ceb40a2bbabfb`，恢复服务为 `swe-new30-resume-20261016.service`。

额外发现：`capabilities` 和 `requires` 是集合，序列化列表顺序随 Python 进程变化，曾导致恢复配置被误判不同。后续修复仅规范化这两个集合字段再比较，任务与 Agent 顺序、模型参数及其他配置继续严格比较；针对恢复和批次的 16 项回归通过。已启动的独立恢复进程继续使用其已记录的代码版本，不在运行中替换代码。
