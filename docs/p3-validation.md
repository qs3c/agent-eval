# P3 验证记录

2026-10-26 在独立 Linux 目录 `/tmp/harness-p3-cJ72` 验证，无真实模型 API 调用、无公开 PR、无生产部署。

| 范围 | 实际结果 | 日志 |
| --- | --- | --- |
| 共享执行器、Guardian 多轮/撤销权限、原生适配与针对性回归 | 74 passed、2 skipped，110.52 秒 | `t12-contracts.log` |
| Recipe 候选、旧原生兼容、发布门禁 | 57 passed，68.38 秒；包含实际 Docker 父子构建与 Harbor 控制题 | `t13-docker.log` |
| 平台 relay 实际容器、预算耗尽、holdout 正常/退化、产品/适配分离 | 22 passed，192.80 秒 | `p3-docker-final.log` |
| 全量回归及启用的 Docker 用例 | 500 passed、8 skipped；3 项因测试目录漏复制 scripts 而失败 | `p3-full.log` |
| 补齐 scripts 后复验全部上述失败项 | 3 passed，0.05 秒 | `p3-prepare-fixed.log` |

因此该源码快照的全部非跳过用例已通过，但不将两次运行写成一次成功的全量命令。8 项跳过包括本轮未重新提供内置 Release/原生镜像环境变量的专门验收，相关基线验证见 [P2 记录](p2-validation.md)。Harbor 输出的 `Task.checksum` 弃用警告保留，未影响结果。

真实 Docker fixture 修改一处 `harness.py` 行为；父、子各自以同一冻结 recipe 构建，运行相同复现断言与负对照，再运行两道控制题和独立 holdout。holdout 退化时即使原题和 targeted 改善也不能提升。PR 草稿为中英文，只包含产品修改；原始源码保持原值。

平台 relay 容器用两次成功调用及第三次 429 验证独立请求预算，检查可信 `improvement` 角色及实际模型覆盖；这是模拟提供商与 fixture harness，不能声称已验证真实模型诊断能力。Guardian 多轮测试拒绝写解题文件，继续读取 snapshot 后提交中文结论；取消、超时、进程和容器清理由确定性监督器负责。

仍需后续阶段验证的内容：未知 GitHub 仓库自动生成/修复 recipe、合并后版本闭环、全新主机下载、迁移与故障演练。完整第三方内部模型/网络覆盖仍未认证，当前结果不自动获得 Comparable。
