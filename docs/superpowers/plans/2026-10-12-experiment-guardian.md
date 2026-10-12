# Experiment Guardian Implementation Plan

> **For agentic workers:** Execute tasks inline in the current authorized session. Steps use checkbox syntax for tracking.

**Goal:** Unattended trial monitoring, autonomous pluggable recovery, auditable incidents and bounded continuation.

**Architecture:** Keep the existing runner for unguarded experiments. Guarded experiments run Harbor Trial in per-attempt subprocesses, supervised independently; JSONL guardian drivers use recorded container tools directly.

**Tech Stack:** Python 3.12, Harbor 0.24.0, Docker, FastAPI, existing JavaScript UI.

**Spec:** `docs/superpowers/specs/2026-10-12-experiment-guardian-design.md`

## Global Constraints

- Linux + Docker for guarded execution; default off; no new paid experiment.
- Immutable historical results; preserve attempts and intervention evidence.
- Guardian agent remains unspecified; command adapter is configurable and trusted.
- Observation and recovery are recorded separately. No predetermined recovery policy table.

## Task 1: Configuration and evidence

Files: create `guardian/config.py`, `guardian/evidence.py`, `guardian/docker.py`; extend `config.py`.

Interfaces: `GuardianConfig`, `capture(root, backend, reason)`, `DockerTarget.snapshot()`, `DockerTarget.exec(command, timeout)`.

- [x] Test invalid mode/timeouts, default compatibility, log size limits and partial capture.
- [x] Implement atomic JSON records, append-only actions and bounded subprocess transport.
- [x] Implement Docker identity matching by exact Compose project and service, bounded inspection without environment variables.
- [x] Run guardian tests and retain the observed integration failure and fix: Windows CRLF broke Linux fixture shebangs; enforce LF for shell scripts.

## Task 2: Direct agent protocol

Files: create `guardian/driver.py` and temporary protocol fixtures in `tests/test_guardian.py` / `tests/test_guardian_docker.py`.

Interfaces: `run_driver(config, incident, backend, active)` sends JSONL context and serves snapshot/exec/finish calls.

- [x] Test a scripted driver requesting a real tool call, malformed output, silent driver, stage change and action budget exhaustion.
- [x] Record request before execution and result afterwards; mark assistance before exec.
- [x] Bound driver lifetime, command lifetime, stdout size and action count; terminate process groups on all exits.
- [x] Run protocol tests and verify driver claims cannot set platform recovery status.

## Task 3: Isolated trial supervision

Files: create `guardian/worker.py`, `guardian/supervisor.py`; extend `run.py`.

Interfaces: `run_guarded(exp, output, agent_config, tasks, agent_id)` schedules frozen `TrialConfig` workers.

- [x] Worker persists Harbor lifecycle stages and current result before cleanup.
- [x] Supervisor monitors progress, creates incidents, dispatches drivers, confirms progress, enforces hard deadlines and terminates failed workers.
- [x] Persist a failure result only if Harbor did not already write one; preserve existing results.
- [x] Test hung worker followed by a successful worker, cancellation cleanup and observer failures.

## Task 4: Product integration and validation

Files: extend `report.py`, `comparison.py`, `console.py`, `web.py`, `static/app.js`; add `docs/experiment-guardian.md`.

- [x] Test assistance counts without changing raw rewards; expose summary and recorded operations through bounded read-only APIs.
- [x] Add incident panel to run detail and guardian-policy compatibility checks.
- [x] Run existing full pytest suite and isolated Linux/Docker protocol and failure-continuation tests.
- [x] Document configuration, protocol, deadlines, provenance and unsupported historical recovery.
- [x] Commit implementation after verification. No production BKCrab restart and no paid model run.

Validation: 96 tests passed on isolated Linux/Docker staging, including a real single-concurrency Harbor queue with a blocked first Agent and a scored second task. Ruff and JavaScript syntax checks passed. See `docs/experiment-guardian.md` for evidence and scope limits.
