# P0 实施与验证记录

日期：2026-10-26。范围：T01 环境诊断与独立准备入口、T02 旧格式兼容基线；P1 尚未实施。

## 交付

- `doctor`：可选 Agent/benchmark、系统与进程能力、Python/工具版本、Docker 可达性、磁盘、CPU/内存、端口、BKCrab eval 源码入口、凭据存在性；机器可读 JSON 与失败退出码。不会安装组件、启动服务或调用模型。
- `prepare-native.sh [all|dsh|bkcrab]`：保留默认 all；DSH 不依赖 Go/BKCrab，BKCrab 不依赖 npm/Node。使用记录型假命令实际执行脚本分支验证依赖隔离。
- CLI 与 RSI CLI 延迟导入执行依赖；Python `-S`（无第三方 site-packages）仍可运行 doctor，不会因为缺 Harbor/Pydantic/fcntl 而在解析前崩溃。
- 真实无模型 controls 的脱敏夹具，另派生中断、部分评分、RSI/复评旧 schema；hash 清单、重复登记、未知来源、原始证据不变及恢复约束回归。
- [安装部署](local-deployment.md)、[兼容迁移基线](workspace-migration.md)、README 与[实施计划](superpowers/plans/2026-10-26-local-single-user-platform.md)同步更新。

## 实际环境与隔离

开发工作区为 Windows；Docker Desktop daemon 未运行。原生 Windows 执行不受支持，doctor 成功输出 platform/process/docker 等失败项并以非零退出；这不计作 Linux 执行验收。

Linux 验证通过 Tailscale SSH 使用 `xavier@100.93.173.10`，在独立目录 `/tmp/harness-p0-JGgSwiv7` 完成。没有覆盖服务器已有 checkout、停止 BKCrab 或重新部署任何服务。

环境：Linux x86_64、Python 3.12.3、uv 0.13.0、Harbor 0.24.0、Linux Docker daemon。doctor 当时检测到 16 logical CPUs、约 82.7 GiB 空闲磁盘。主机和 Docker 镜像缓存已有，**不是一台全新裸机安装验收**；第二轮使用全新 `.venv` 与锁文件安装，未沿用旧项目 Python 环境。

传输 Windows 工作树时发现 shell CRLF 会导致 Linux `pipefail` 解析失败，修正传输副本及本次修改脚本的行尾后重测；仓库已要求 shell LF，本次另给字节哈希夹具明确 LF，避免检出方式改变 fixture hash。

## 验证命令与结果

在隔离目录使用项目锁文件：

```bash
export PATH=/home/xavier/Desktop/github/agent-harness-eval/.tools:$PATH
uv sync --locked --python 3.12
uv run --no-sync agent-harness-eval doctor --json
LITELLM_LOCAL_MODEL_COST_MAP=True uv run --no-sync pytest -q
LITELLM_LOCAL_MODEL_COST_MAP=True uv run --no-sync agent-harness-eval run \
  experiments/controls.json --output artifacts/p0-installed-controls
```

`uv sync` 新建 `.venv`，按锁文件解析 100 个包、安装 98 个包，项目 editable build 成功。doctor controls 返回 `ok: true`。

初轮目标回归 80 passed，包括 doctor、准备脚本、旧工作区、workbench repair、Console、RSI topics、manifest 和 contracts；随后增加端口占用、BKCrab 源码入口、最低 Python 版本边界测试。最终代码的全量回归：**375 passed，11 skipped，67.84 秒**。`git diff --cached --check` 通过，新增文档相对链接检查通过。

| Docker controls | 计划 / 已评分 | 平均 reward | 执行错误 | 缺失 |
| --- | --- | --- | --- | --- |
| positive | 2 / 2 | 1.0 | 0 | 0 |
| negative | 2 / 2 | 0.0 | 0 | 0 |

先借用已有 Python 环境验证一次，随后在独立锁定环境验证一次，结果一致；共 8 次无模型任务尝试。费用与 token 不可知字段保持 null，没有模型推理调用。首轮 LiteLLM 导入曾尝试下载公开价格表并回退本地；后续设置 `LITELLM_LOCAL_MODEL_COST_MAP=True` 使用本地表。这与模型付费调用不同，也不将本轮称为完全无网络验收。

服务器保留 `install.log`、`doctor-final.json`、`final-tests.log`、`installed-controls.log` 及两轮 `artifacts/` 原始输出；本机 `.cache/p0/` 保存验证摘要与构建传输文件，不进入 Git。长期可回归依据是提交的脱敏 fixture 和测试，不依赖临时目录永久存在。

## 原有未提交改动保护

以下 SHA-256 在实施前后相同，文件未加入本次提交：

| 文件 | SHA-256 |
| --- | --- |
| `src/agent_harness_eval/resources/live_model.py` | `71ad395052e0253d1ca413735b5a8df1ddcbc3671726251a6d326f239281e19b` |
| `tests/test_relay_roles.py` | `2e31bac14b10f9fdf6adc6350238b80519c98532ffc65610fc833e2ffa75e06e` |
| `docs/interview/bkcrab-portal-incident-interview.md` | `60d8e0876370aa54623ba021989bb8bc024ad082cace8f804469e83dc3b28b8c` |

测试副本包含原有 role 改动，未用旧提交覆盖它们；测试通过不代表替用户提交了这些改动。

## 验收边界

- 未进行真实模型连通性/付费实验，也未发布 PR 或部署服务。
- 未实际重新下载构建所有原生 Agent；P0 对独立构建入口使用记录型命令验证。原生 Agent 全链路独立认证属于 P2。
- 全量测试中 Docker opt-in 等环境专用测试仍遵循原跳过条件；实际执行的 Docker 验收是本页明确列出的 controls。
- 只读历史登记写入新索引是允许的，原始 manifest/result/RSI case 不变；没有实施新 schema 迁移、故障回滚引擎或新 Manager。
- WSL2、Windows 原生执行与全新裸机完整安装仍未认证。doctor 预检通过也不替代具体 Agent/benchmark 的 conformance。
