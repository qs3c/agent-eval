# 双路径接入与公共执行器实施方案

**目标：** 源码链接之后选择一键接入或自行接入；内置执行器仅 DSH、OpenCode、Hermes；候选统一由平台验收。

**设计依据：** 用户在本轮明确要求两条页面路径、三种开源执行器、可复制外部接入说明，并要求移出私人被测项目内容。

**架构：** 使用现有 Recipe Release 作为原生执行器产物，通用容器适配器传递标准请求并挂载只读目标源码。接入请求冻结执行器 Release、模型和预算，不再借用 RSI 的当前选择。外部接入只生成说明及提交配方；两条路径最终进入 source repair / build / conformance。

**技术栈：** Python、FastAPI、SQLite、Docker、原生 JavaScript、pytest、node:test。

## 实施步骤

- [x] `platform_agents/releases.py`：三个公共执行器目录、固定 Release 解析与禁用检查；`scripts/prepare-rsi-runtime.py` 准备不含私人 Agent 的通用容器。
- [x] `rsi/native_adapter.py`：使用冻结配方的标准入口启动三个原生 Agent，保留模型角色、中继、只读挂载、时限与结果检查。
- [x] `integrations/assistant.py`、`api.py`、`commands.py`：显式选择执行器和模型；支持首次观察源码；提供只读接入说明；所有候选仍走统一准入。
- [x] `static/integrations.js`：源码输入、两种方式、自动接入准备状态及费用确认、可复制说明、外部配方提交、共享进度与验收显示。
- [x] 公共目录与文档清理：移出私人专用文件、配置、历史实验材料；本机仓库外备份，不改写 Git 历史，不修改已有实验原始证据。
- [x] 验证：执行器选择/快照/模型协议测试，外部候选与自动候选统一准入测试，页面交互测试，公开内容扫描与 Python/JS 语法检查；Linux 检查在独立临时目录运行，不部署。

## 约束

生成成功不是验收成功；Runnable 与题目得分分开；Comparable 保持未认证。保留原生 Agent 循环，禁止以模拟产物冒充真实运行。已有未提交的模型中继修改及用户访谈材料不覆盖。
