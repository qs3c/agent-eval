"""Never let a test worker access the user's real host-wide grading queue."""

import pytest


@pytest.fixture(autouse=True)
def isolated_regrading_state(tmp_path, monkeypatch, request):
    # Only service/queue tests need this isolation; don't initialize queue modules
    # or add filesystem entries to unrelated timing and log-contract tests.
    names = {"Console", "create_app", "regrade_batch", "regrading", "batching", "image_lifecycle"}
    values = vars(request.module)
    if not (
        names.intersection(values)
        or any(
            names.intersection(
                getattr(getattr(value, "__code__", None), "co_names", ())
            )
            for value in values.values()
        )
    ):
        return

    from agent_harness_eval import regrading

    root = tmp_path / "grading-state"

    def state_root():
        root.mkdir(exist_ok=True)
        return root

    monkeypatch.setattr(regrading, "state_root", state_root)
