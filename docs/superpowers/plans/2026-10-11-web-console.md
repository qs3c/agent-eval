# Web Console Implementation Plan

> Execute inline in this session, task by task. The referenced executing-plans/subagent-driven-development skills are not installed; use the concrete steps and checks below without delegating.

**Goal:** Create, observe and compare local Harbor experiments in a Chinese browser console.

**Architecture:** FastAPI serves same-origin buildless assets. SQLite records a serial experiment queue, and an owned CLI subprocess runs each experiment. Existing artifacts and reports remain authoritative.

**Tech Stack:** Python 3.12, FastAPI 0.143.0, Uvicorn 0.54.0, SQLite, native HTML/CSS/JavaScript.

**Spec:** `docs/superpowers/specs/2026-10-11-web-console-design.md`

## Global constraints

- Default bind 127.0.0.1; no public hosting or automatic paid evaluations.
- Reuse existing CLI, configuration validation and Harbor; retain 23 existing tests.
- Missing usage remains unknown. Harbor scores are not official SWE regrades.
- Template fields and registered run IDs only; no arbitrary import paths or shell input from browsers.
- Do not rewrite historical artifacts. Reject symlink escapes and cross-site writes.

### Task 1: Durable queue and evidence reader

Files: create `src/agent_eval/console.py`, `tests/test_console.py`.
Interface: `Console(root: Path, allow_live=False)`; `templates()`, `submit(data)`, `runs()`, `detail(id)`, `logs(id)`, `compare(left,right)`, `start()`, `close()`.

- [x] Write tests that create a temporary project with a minimal experiment and exercise invalid fields, missing dependencies and historical incomplete reports.
  ```python
  with pytest.raises(ValueError):
      console.submit({"template": "controls", "implementation": "os:system"})
  ```
- [x] Implement strict submission models, template-relative absolute paths, immutable copied tasks/agent packages at submission, SQLite parameterized statements and atomic queue claim.
- [x] Add a worker with process-group termination, inherited execution lock and conservative restart blocking if an orphan or Docker container remains.
- [x] Read historical reports without calling the report writer; compute current progress from completed result files; bound log reads and filter known credentials.
- [x] Run `.venv/bin/pytest tests/test_console.py -q`; verify unknown cost, missing reports and compatibility differences.

### Task 2: API, command and browser interface

Files: create `src/agent_eval/web.py`, `src/agent_eval/static/index.html`, `src/agent_eval/static/app.js`, `src/agent_eval/static/style.css`, `tests/test_web.py`; modify CLI, dependencies and ignore rules.
Interface: `create_app(root: Path, allow_live=False)` exposes templates, runs, details, logs and comparison through the approved routes.

- [x] Add API tests using TestClient for Origin/Host rejection, unknown ID, invalid submission, denied live run and static assets.
  ```python
  assert client.post('/api/runs', headers={'Origin':'https://evil.example'}, json={}).status_code == 403
  ```
- [x] Add `serve --root --port --allow-live`, lifespan worker ownership and bounded request bodies. Pin installed FastAPI/Uvicorn versions with uv.
- [x] Build an accessible Chinese sidebar/list/detail/create/compare interface using DOM textContent for data, native form validation and two-second read polling.
- [x] Keep same-benchmark incompatible runs viewable with reasons. Show expected/recorded trials rather than synthetic progress.
- [x] Run `.venv/bin/pytest -q` and `node --check src/agent_eval/static/app.js`.

### Task 3: End-to-end validation and handoff

Files: update README; create `WEB_VALIDATION.md`.

- [x] Start `.venv/bin/agent-eval serve --port 8765` and verify `/api/templates` and `/` return HTTP 200.
- [x] Submit one offline controls experiment, wait for actual Harbor completion, verify positive=1 and negative=0. Query after refresh and compare compatible historical reports.
- [x] Use browser UI to inspect desktop and narrow viewport, exercise creation/details/comparison and inspect for console errors.
- [x] Build wheel and inspect archive for static assets, document local start, live opt-in, interrupted recovery and validation limits.
- [x] Run final tests and whitespace checks; commit reviewed implementation locally and hand off URL. No automatic push/deployment is part of this change.
