# Legacy workspace fixture

来源：2026-10-26 在 Linux x86_64 / Python 3.12.3 / Harbor 0.24.0 上实际执行
`run experiments/controls.json --output artifacts/p0-controls` 的 manifest/report。
正对照两题 1、负对照两题 0、无执行错误；没有模型调用。

脱敏/裁剪：任务路径改为相对路径；随机 trial 后缀改为 fixture；日期归一化；
仅保留结果读取所需字段，result.json 从实际 report 的 task/metrics/usage 重建，
不是完整 Harbor result 归档。移除 workbench inventory 引用与源码细节；
成本/token 的 null 不补零。completed 的任务 hash 保留自实际实验。

interrupted、partial-grade、RSI 记录与队列 SQL 是人为构造的旧格式故障状态，
不声称实际运行过 RSI 或 SWE 官方评分。partial-grade 有意缺失任务来源，
用于确保旧数据不会自动获得“可比较”结论。

SQL 从当前旧 schema 生成，测试在临时目录执行，将 `__WORKSPACE__` 替换为测试路径。
只还原 fixture，不允许对用户数据库执行这些脚本。hashes.json 核验除自身与本说明
之外的全部文件字节；此目录使用 LF。生成新基线必须保留旧 fixture，并解释新增格式。
