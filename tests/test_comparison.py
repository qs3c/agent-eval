import pytest

from agent_harness_eval.comparison import summarize


def config(attempts=1):
    return {
        "agents": [{"id": "a"}, {"id": "b"}],
        "attempts": attempts,
        "benchmark": {"tasks": ["django", "other"], "primary_metric": "reward"},
    }


def row(agent, task, score, **kwargs):
    return {"agent": agent, "task": task, "metrics": {"reward": score}, **kwargs}


def test_multitask_scores_and_usage_coverage():
    rows = [
        row("a", "django", 1, input_tokens=100),
        row("a", "other", 0),
        row("b", "django", 0),
        row("b", "other", 1),
    ]
    data = summarize(config(), rows, "resolved", True)
    assert data["agents"]["a"]["score"] == 0.5
    assert data["agents"]["a"]["solved_tasks"] == 1
    assert data["tasks"]["django"]["b"]["score"] == 0
    assert data["agents"]["a"]["usage"]["input_tokens"] == {
        "total": None,
        "known": 100,
        "recorded": 1,
        "expected": 2,
    }
    assert data["agents"]["a"]["usage"]["cost_usd"]["known"] is None


def test_repeated_attempts_are_task_balanced_not_best_of():
    rows = [row("a", "django", x) for x in (1, 0)] + [row("a", "other", 1)] * 2
    data = summarize(config(2), rows, "resolved", True)
    assert data["tasks"]["django"]["a"]["score"] == 0.5
    assert data["agents"]["a"]["score"] == 0.75
    assert data["agents"]["a"]["successes"] == 3


def test_missing_errors_invalid_scores_stay_in_denominator():
    rows = [
        row("a", "django", 1),
        row("a", "django", 1, exception={}),
        row("a", "other", 2),
    ]
    data = summarize(config(2), rows, "resolved", True)["agents"]["a"]
    assert data["score"] is None
    assert data["provisional_score"] == 0.25
    assert (data["missing"], data["errors"], data["unscored"]) == (1, 1, 2)


@pytest.mark.parametrize("extra", [row("a", "django", 1), row("a", "outside", 1)])
def test_extra_or_unexpected_trials_do_not_inflate_score(extra):
    data = summarize(
        config(), [row("a", "django", 1), row("a", "other", 1), extra], "resolved", True
    )
    assert data["agents"]["a"]["score"] is None
    assert data["agents"]["a"]["provisional_score"] is None


def test_nonbinary_metric_keeps_units_and_running_is_provisional():
    rows = [row("a", "django", -2), row("a", "other", 8)]
    assert summarize(config(), rows, "metric", True)["agents"]["a"]["score"] == 3
    assert summarize(config(), rows, "metric", False)["agents"]["a"]["score"] is None


def test_unknown_nan_boolean_usage_not_treated_as_zero():
    rows = [
        row("a", "django", 1, input_tokens=True),
        row("a", "other", 1, input_tokens=float("nan")),
    ]
    usage = summarize(config(), rows, "resolved", True)["agents"]["a"]["usage"][
        "input_tokens"
    ]
    assert usage["total"] is None and usage["known"] is None
