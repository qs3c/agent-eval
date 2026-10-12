# RSI executor selector implementation plan

> Implement inline in this session; the user explicitly requested a working BKCrab / DSH frontend selector.

**Goal:** Configure and select the executor in the browser without editing profiles or entering commands.

**Architecture:** A server-owned catalog discovers existing native runtime artifacts and target repositories. Browser settings select an executor and model; generated profiles freeze that choice per candidate. Native adapters run in disposable Docker containers with a read-only evidence mount and writable candidate workspace/output.

**Tech Stack:** FastAPI / Pydantic, plain JavaScript, Docker, existing BKCrab and DSH native loops.

**Spec:** User request on 2026-10-12: frontend dropdown with BKCrab and DSH; no manual JSON configuration.

## Constraints

- Executor is separate from the source Agent being improved.
- Browser inputs cannot supply commands, repository paths, Docker arguments or credential destinations.
- Saving settings does not start an Agent or invoke a paid model.
- API never returns credential values. Existing custom profiles keep working.
- Preserve frozen candidate configurations and existing evidence.

## Tasks

- [x] Add `rsi/executors.py`: `catalog(root)`, `save_settings(root, request)`, `builtin_profile(root, executor, target)`; fixed IDs, model validation, private credential storage and native dependency detection. Test settings persistence, unknown keys/IDs, secret redaction and target/executor separation.
- [x] Add `rsi/native_adapter.py` and a Docker runtime recipe: run the selected native loop with the existing JSON diagnosis protocol. Bind only candidate workspace, evidence and output; clean containers on termination. Add mocked subprocess tests and an opt-in offline Docker smoke test.
- [x] Extend `rsi/api.py`, `runner.py` with catalog/settings endpoints and generated built-in profile snapshots; preserve the custom profile path.
- [x] Replace the frontend manual-config warning with the executor settings card and dropdown; expose the same selection in candidate creation. Test save errors, persisted choice and disabled state explanations.
- [x] Build the shared runtime on the server, run Linux/API and frontend tests, inspect browser screenshots, commit/push and update the idle service. Preserve all historical cases and source checkouts.

Acceptance: 53 Linux checks (including both offline native Docker starts) and 10 frontend tests passed. Deployed to port 18766; browser saved BKCrab, candidate form inherited it and allowed DSH while retaining the BKCrab target. Existing 2 cases, 0 candidates and 0 actions preserved; no paid model invocation.
