# Agent Harness Eval Implementation Plan

**Goal:** independent Harbor experiments with BKCrab and verified DSH Standard, extensible benchmarks and auditable results.

**Architecture:** reuse Harbor installed-agent and task contracts. Add local experiment validation, source snapshots, two native adapters, trace conversion, SWE export/regrade and reports.

**Tech Stack:** Python 3.12, Harbor 0.24.0, Docker, Go 1.25, pinned DSH runtime.

**Spec:** ../specs/2026-10-11-agent-harness-eval-design.md

## Global Constraints

- Preserve existing BKCrab and DSH changes; no push or deployment.
- DSH Standard requires verified preset composition; sdk-minimal is excluded.
- User authorized one SWE problem per agent, official DeepSeek V4.1 Flash, unbounded spending on 2026-10-11. Fake-model evidence is never benchmark performance.
- Source and task manifests contain hashes; secrets are environment references only.
- Run and score failures remain distinct; unknown observations are nullable.

## Tasks

- [x] Dependency lock and source inspection: pin Harbor, inspect installed signatures, pin DSH and record provenance.
- [x] Experiment CLI: `load_experiment(path)` validates JSON and resolves paths; `run_experiment(config, output)` writes manifest and submits Harbor jobs. Reject malformed or duplicate configurations before any subprocess runs. Check with `python -m pytest tests/test_config.py`.
- [x] Native adapters: `agent_harness_eval.agents.bkcrab:Bkcrab` and `agent_harness_eval.agents.dsh:DeepSeekStandard` install artifacts and run real executables using Harbor's installed-agent API. Add BKCrab `cmd/bkcrab-eval` only; verify using a local mock OpenAI endpoint and real filesystem actions.
- [x] DSH composition audit: compare pinned Standard preset to the launch composition, export audit/configuration hashes and reject missing/mismatched required entries. Verify with an intentionally removed compaction entry and a valid composition.
- [x] Evidence/report: native logs, coverage, typed artifacts, ATIF, nullable usage and per-benchmark metrics. Test success, ordinary failure, timeout, absent scores and malformed outputs.
- [x] Benchmarks: non-SWE file tasks with deterministic positive and negative controls; SWE preparation references upstream task adapter, captures patches and exports official predictions. Test committed/staged/untracked changes and fresh-environment regrade command construction.
- [x] Integration: run actual Harbor containers; verify two agents/two tasks, isolated homes, durable logs and score reports. Record exact commands and prerequisites in README and validation report.

Implementation is inline in this chat. The optional executing-plans/subagent-driven-development skills are not installed; no delegation is required for this implementation.
