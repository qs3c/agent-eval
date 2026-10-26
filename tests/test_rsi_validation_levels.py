import pytest

from agent_harness_eval.rsi.validation import report, targeted_passed
from agent_harness_eval.rsi.publication import readiness


def levels():
    return {name: {"parent_commit": "a" * 40, "candidate_commit": "b" * 40,
                   "status": "passed", "evidence": [name + ".json"], "assessment": "improved"}
            for name in ("build", "targeted", "benchmark-replay")}


@pytest.mark.parametrize("level,status", [("build", "failed"), ("targeted", "inconclusive"), ("benchmark-replay", "failed")])
def test_targeted_pass_does_not_hide_replay_build_failure_or_missing_controls(level, status):
    evidence = levels()
    evidence[level]["status"] = status
    value = report(parent_commit="a" * 40, candidate_commit="b" * 40, recipe_digest="recipe", model_policy_digest="model", levels=evidence)
    assert not value["promotable"]
    assert value["assessment"] == "inconclusive" and value["issues"]


def test_stale_and_missing_holdout_do_not_promote():
    inputs = dict(parent_commit="a" * 40, candidate_commit="b" * 40, recipe_digest="recipe", model_policy_digest="model")
    evidence = levels()
    assert report(**inputs, levels=evidence)["promotable"]
    assert not report(**inputs, levels=evidence, required=("build", "targeted", "benchmark-replay", "holdout"))["promotable"]
    adverse = {**evidence, "holdout": {**evidence["build"], "assessment": "regressed"}}
    assert not report(**inputs, levels=adverse)["promotable"]
    evidence["targeted"]["candidate_commit"] = "c" * 40
    assert not report(**inputs, levels=evidence)["promotable"]
    record = {"parent_commit": "a" * 40, "commit": "d" * 40, "profile": {"integration_recipe": "frozen"}, "validation": report(**inputs, levels=levels())}
    assert not readiness(None, record)[0]


def test_compile_errors_are_not_positive_reproduction_and_negative_control_is_required():
    evidence = {"parent": {"reproducer": {"status": "assertion_failed"}, "negative": {"status": "passed"}},
                "candidate": {"reproducer": {"status": "passed"}, "negative": {"status": "passed"}}}
    assert targeted_passed(evidence)
    evidence["parent"]["reproducer"]["status"] = "error"
    assert not targeted_passed(evidence)
    evidence["parent"]["reproducer"]["status"] = "assertion_failed"
    del evidence["candidate"]["negative"]
    assert not targeted_passed(evidence)
