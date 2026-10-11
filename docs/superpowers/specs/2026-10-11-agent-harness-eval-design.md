# Agent evaluation workbench

Status: scope approved in chat on 2026-10-11. Live DeepSeek model execution awaits a monetary budget. Implementation validation must distinguish mock-provider runs from model capability measurements.

## Objective and boundaries

Run BKCrab and the official DSH Standard configuration on the same SWE tasks, retaining reproducible results and diagnosis evidence. Adding an agent must not require changing benchmark grading; adding a benchmark must not require changing agent implementations. Python and Harbor own orchestration; existing agents retain their native loops. This repository is independent from BKCrab. No web service, trace database, or replacement scheduler.

## Composition

An experiment selects versioned agent configurations, a fixed local Harbor dataset, model endpoint, attempts, resource limits, and timeout. Harbor owns container lifecycle, concurrency, cancellation, logs, and verifier invocation. Custom installed agents start real executables in the task container. Benchmark preparation produces Harbor tasks; existing upstream integrations should be reused. Results retain native metrics rather than assuming every task has a patch or a boolean resolved field.

## Reproducibility and trust boundaries

Record source revision plus dirty-source content digest, runtime version, model configuration without credentials, task hashes, agent configuration, and scoring versions. Save immutable run manifests before execution; do not silently resume or overwrite a run. Every trial gets fresh home/session/workspace. Reference solutions and verifier assets belong to the evaluation side. Restrict agent networking according to experiment policy. Do not export secrets in configuration or traces. Missing usage and observability are unknown, never zero. Runtime failure, grading failure, and task score are separate.

## DSH Standard

`sdk-minimal` is forbidden in the main experiment. Application profile (`sdk`, `web`, `headless`) and the Web `standard` agent preset are different concepts. Pin the source and runtime; derive the headless/programmatic configuration from the official Standard preset and inspect effective prompt, tools, compaction and delegation. Save the complete configuration and a machine-readable audit. Do not label a non-equivalent SDK default as Standard. Audit failures must block a DSH Standard run.

## BKCrab

Add a standalone executable under BKCrab's cmd directory so its Go internal packages remain usable. Reuse the production Agent constructor and turn path. Isolate state and point filesystem and shell tools at the task workspace. Running inside Harbor provides isolation; do not launch BKCrab's separate default sandbox. Record the actual provider request/response boundary and tool events where available. State which production services are disabled in the evaluation profile. Preserve all pre-existing worktree edits.

## Benchmark and evidence contract

Use native Harbor task/environment/verifier formats. SWE-specific code owns baseline revision, complete patch extraction (tracked, staged, committed and untracked files), predictions export and official harness regrading. Generic tasks can produce typed file artifacts and numeric metrics. Preserve native logs, ATIF where convertible, actual model requests, usage, termination cause and coverage declarations. Reports group by benchmark and protocol, include failed runs and denominators, compare paired tasks and avoid cross-benchmark blended scores.

## Validation

Unit tests cover config rejection, isolation/secret handling, missing observability, error aggregation, DSH audit and SWE patch handling. Exercise two small non-SWE file tasks through Harbor using deterministic agents and a local fake model for the real agent entrypoints. Environment oracle and negative controls validate scoring. A real SWE smoke and official regrade require available task images; record blocked external prerequisites without pretending they passed. Paid models require the user's budget. First model choice: DeepSeek official, exact model and budget pending.

## References

- https://docs.harborframework.com/agents/custom-agents
- https://docs.harborframework.com/tasks/overview
- https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/architecture.md
- https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/bundle/web-app/cordis.patch.yml
