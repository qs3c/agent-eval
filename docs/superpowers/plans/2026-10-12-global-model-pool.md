# Global model pool implementation plan

**Goal:** One global Models module with shared entries and effective scene bindings.
**Spec:** `docs/superpowers/specs/2026-10-12-global-model-pool-design.md`.
**Execution:** Inline in the authorized current task.

- [x] Implement `model_pool.py`: validated entries, locked atomic CRUD, private versioned credentials, migration, `resolve(scene, model_id)` and `environment(config)` snapshots. Add `/api/models` and `/api/model-bindings` routes with sanitized validation errors.
- [x] Integrate RSI catalog/settings and native command snapshots with pool selections and both API endpoints. Keep legacy candidate arguments readable. Tests assert editing defaults cannot redirect an existing snapshot.
- [x] Integrate Console submission/worker snapshots and guardian model config/driver. Preserve offline/mock templates and observe mode. Adapt the live relay to configured compatible endpoints without applying DeepSeek sampling settings to other providers.
- [x] Add `models.js` / `models.css`, top-level navigation and CRUD/bindings UI. Replace RSI embedded settings with pool selection/link; add pool selectors to experiment creation. Verify submission values, missing-configuration guidance and error states.
- [x] Run focused Linux and JS tests, inspect desktop/mobile screenshots, commit/push, pull and restart only the idle RSI service. Verify legacy model migration and scene selections without invoking paid models.

Validation before deployment: full Linux suite 125 passed / 7 opt-in checks skipped; after adding a real guardian child-process test, focused pool/guardian/executor suite 34 passed / 2 Docker checks skipped. Frontend 14 passed. Desktop 1440px and mobile 390px reviewed; mobile document has no horizontal overflow. No paid model calls.

Deployed b80417e to the idle RSI service on port 18766. Browser verified migration (one model, four scene defaults), saved scene assignments, and confirmed RSI contains pool selection with no embedded credential form. Historical counts remain two cases, zero candidates and zero actions. Screenshot: `.cache/rsi-ui/global-model-pool-deployed.jpg`.
