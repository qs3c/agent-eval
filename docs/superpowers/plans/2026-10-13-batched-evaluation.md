# Batched Evaluation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 单个实验按最多 20 题准备、运行、复评及清理，限制峰值磁盘且保留可审计证据。

**Architecture:** run.py 冻结全部小输入和共享 runtime，batching.py 管理批次和状态。既有守护工作进程管理单题，retention.py 在结束/兜底后按有界策略保留停止容器。官方复评在批次清理前执行，恢复只跳过已落盘尝试。

**Tech Stack:** Python 标准库、Pydantic、Harbor 0.24.0、Docker。

**Spec:** docs/superpowers/specs/2026-10-13-batched-evaluation.md

## Global Constraints

- 80 题名单、排除的 21 题、两个冻结 Agent 与模型保持一致。
- 并发 2；assist，40 轮/600 秒；每题 2 CPU/4096 MiB/1800 秒。
- 默认保留上限 3 个、20 GiB、24 小时；异常/辅助结果继续显式标记。
- 不覆盖历史结果，不重跑已完成或已失败尝试；不清其他服务镜像/数据卷。

### Task 1: Shared freezing and batch lifecycle (primary agent)

Files: config.py, freezing.py, batching.py, run.py, cli.py, benchmarks/swe.py, tests/test_batching.py.

Interfaces: `freeze_tree(source, target, shared)` copies source once and hardlinks only owned runtime archives. `run_batches(exp, output, tasks, agent_configs, *, resume, manage_signals)` calls `run_guarded(..., retention=policy, resume=resume)` and writes batch-state.json.

- [x] Regression: two tasks have equal archive inode in the frozen tree, but a different inode from source; source mutation cannot alter frozen bytes.
- [x] Regression: size=1, two tasks/two Agents executes prepare-a, both Agents-a, regrade-a, cleanup-a, then b; one manifest/report counts four attempts.
- [x] Regression: resume skips completed batch and terminal trial results; changed frozen config or uncertain partial attempts fail explicitly.
- [x] Implement `BatchConfig(size=20, prepare_swe=True, official_regrade=True, retain_count=3, retain_gib=20, retain_hours=24, min_free_gib=20)` with schema validation.
- [x] Implement lazy pinned-image preparation and current-batch-only regrading; cleanup refuses referenced images and logs actual freed space.
- [x] Run `PYTHONPATH=src .venv/bin/python -m pytest tests/test_batching.py tests/test_manifests.py tests/test_swe.py -q`.

### Task 2: Abnormal environment retention (delegated agent)

Files: guardian/retention.py, worker.py, supervisor.py, tests/test_retention.py, isolated Docker test.

Interfaces: `run_guarded(..., retention: dict|None=None, resume: bool=False)`; trial retention-policy.json supplies count/disk/TTL limits. retention.json records container identity, state, evidence reason, scrubbing and actual cleanup.

- [x] Write/run regression for normal reward=0 cleanup, exception/assisted retention, limits, missing evidence and resume behavior.
- [x] Use Harbor `keep_containers=True` only under explicit retention policy; stop target container, remove live credentials, then retain or remove by actual policy.
- [x] Supervisor fallback preserves available diagnostic evidence and terminates stalled execution; failures are recorded rather than claimed cleaned.
- [x] Run focused unit and opt-in isolated Docker checks; no paid model calls or production cleanup.

### Task 3: Console, deployment and live continuation (primary agent)

Files: console.py, static/app.js, docs/batched-evaluation.md, scripts/run-swe-live-80.py, experiment template.

- [x] Expose batch-state.json in run details, use shared freezing in queue submission, and display batch/phase without changing scoring.
- [x] Switch SWE80 launcher to platform batching/resume; freeze selected model config once, write preflight and preserve selection/hash evidence.
- [x] Full Python suite, focused Docker lifecycle controls, JS syntax, diff checks. Commit only requested feature files after verification.
- [x] Deploy idle main console, validate API, then start first 20-task batch at concurrency 2/assist. Confirm actual native model requests and independent guardian workers.
- [x] Document observed results and limits; retain remaining batches as the same active experiment.

**Deployment evidence:** 2026-10-13 local commit b51b273, main console restarted; SWE80 batch 1/4 has two active guardian workers and native request logs for pylint-6903 and django-16901. Formal results are still pending. Evidence: logs/swe-live-80-pipeline/start-verification.json.
