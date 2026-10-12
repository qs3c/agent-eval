# RSI improvement workbench

Approved direction: the user requested continuing the already discussed RSI subsystem after the guardian. The execution Agent is pluggable, with no default provider. Implement inline without repeating approval for these established requirements.

## Design

Choose a bounded, repeatable improvement cycle with persistent lineage. A prompt-only diagnosis tool would not close the loop; an unbounded population search would introduce spending and selection policies the user has not configured. Each candidate is a reusable archive node; users can branch another candidate from a validated parent. Do not equate this first cycle runner with DGM's automatic population search.

1. Cases freeze selected eval results, native traces, verifier outputs and guardian/manual intervention records. Every exported file has its source classification, original relative path, byte range, hash and truncation flag. Preserve unknowns; never generate a root-cause assertion from an exception type. Exclude task solutions, credentials and unrelated agent traces. Case creation does not execute a model.
2. Administrator profiles select target repository/ref, a versioned command adapter, trusted check commands, optional build outputs/eval template and optional GitHub PR destination. Browser requests select profile IDs only; they cannot submit executable commands or arbitrary filesystem paths.
3. Candidates use independent Git clones pinned to an exact parent commit. The original checkout is untouched. The adapter receives the evidence directory, workspace and result schema via JSON stdin, edits its workspace and writes a structured diagnosis with evidence citations. It does not create platform records or publish a PR. The platform captures output, enforces a process deadline, commits the patch and runs configured checks against that exact commit.
4. `validated` means checks passed, not improved. Optional paired reevaluation builds both exact commits and runs the same frozen experiment contract via the existing Harbor runner, retaining both original reports. Missing results, assisted attempts, mismatched tasks/contracts or unavailable inputs yield an inconclusive comparison. Only complete comparable measurements produce improved/equal/regressed labels. Task scores and execution failures stay distinct. Live reevaluation requires the existing explicit spending acknowledgement and server live gate.
5. Draft PR publication is an explicit action using the configured repository/base, recorded diff, diagnosis, check evidence and optional reevaluation summary. Never merge/deploy automatically. Publishing is idempotent by candidate branch; retain partial failure states. Failure and non-winning candidates remain in the archive with parent relationships.
6. Console exposes an RSI section: cases, evidence index, profiles, candidates, phase/validation/reevaluation/PR status and lineage. Durable action records allow the existing single console worker to execute RSI CLI subprocesses when no eval is queued. A interrupted action is shown as interrupted and never silently rerun. CLI supports the same workflow without the web server.

## Limits and validation

Trusted adapters/checks/builds run with host permissions inside the isolated checkout; this is not an adversarial code sandbox. Limits bound execution and output, not API spend of arbitrary custom scripts. Credentials are passed through explicit environment names. Raw evidence remains local; public APIs return indices and summaries, never complete native reasoning traces. No actual model, paid run, PR publication or production deployment is selected by implementation tests.

Test with local fixture Git repositories and command adapters: immutable evidence, truncation, stale/injected paths, parent integrity, failed/silent adapter, invalid citations, no patch, failed checks, checked commit integrity, parent branching, paired Harbor reevaluation, publication retries, persisted queue recovery and web request boundaries. Integrate the two existing BKCrab failures as diagnostic cases without claiming an already verified fix.

References inspected 2026-10-12: https://github.com/jennyzzt/dgm , `DGM_outer.py` (archive and parent selection), `self_improve_step.py` (modification and empirical evaluation). PR delivery is this platform's integration, not a claim about DGM's implementation.
