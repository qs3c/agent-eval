import hashlib
import json

from agent_harness_eval.guardian.evidence import write
from agent_harness_eval.measurement.results import results
from agent_harness_eval.provenance import digest
from agent_harness_eval.regrading import EVALUATOR


def fixture(output, *, repeats=1):
    experiment = {"agents": [{"id": "own"}], "attempts": repeats,
        "benchmark": {"id": "swe", "tasks": ["task"], "primary_metric": "reward",
                      "dataset": {"name": "princeton-nlp/SWE-bench_Verified"}}}
    record = {"instance_id": "task", "base_commit": "frozen"}
    write(output / "tasks/task/tests/config.json", record)
    trial = output / "jobs/own/first"
    write(trial / "result.json", {"started_at": "2026-01-01", "finished_at": "2026-01-02"})
    (trial / "verifier").mkdir()
    (trial / "verifier/model.patch").write_text("patch")
    rows = [{"agent": "own", "task": "task", "trial": "first", "result": "jobs/own/first/result.json",
             "exception": None, "metrics": {"reward": 1}, "cost_usd": None}]
    return experiment, rows, record


def official(output, record, *, resolved=0):
    prediction = {"instance_id": "task", "model_patch": "patch", "model_name_or_path": "own"}
    fingerprint = hashlib.sha256(json.dumps([record, prediction, EVALUATOR], sort_keys=True).encode()).hexdigest()
    report = output / "batches/001/official/unit/report.json"
    write(report, {"submitted_ids": ["task"], "submitted_instances": 1, "resolved_instances": resolved})
    write(output / "batches/001/official/progress.json", {"evaluator": EVALUATOR, "units": {"unit": {
        "agent": "own", "task": "task", "attempt": 1, "status": "completed", "input_sha256": fingerprint,
        "report": str(report.relative_to(output)), "report_sha256": digest(report)}}})
    return report


def test_official_score_is_primary_and_unresolved_is_complete(tmp_path):
    experiment, rows, record = fixture(tmp_path)
    official(tmp_path, record, resolved=0)
    data = results(tmp_path, experiment, rows, "completed", "resolved")
    assert data["agents"]["own"]["score"] == 0
    assert data["harbor"]["agents"]["own"]["score"] == 1
    assert rows[0]["metrics"]["reward"] == 1  # Original evidence stays unchanged.
    assert data["states"]["scoring"] == "complete"
    assert data["agents"]["own"]["usage"]["cost_usd"]["unknown_fraction"] == 1


def test_pending_and_partial_scoring_never_produce_total_success_rate(tmp_path):
    experiment, rows, record = fixture(tmp_path, repeats=2)
    pending = results(tmp_path, experiment, rows, "completed", "resolved")
    assert pending["states"]["scoring"] == "pending"
    official(tmp_path, record, resolved=1)
    data = results(tmp_path, experiment, rows, "completed", "resolved")
    assert data["states"]["scoring"] == "partial"
    assert data["denominators"] == {"planned": 2, "executed": 1, "scored": 1}
    assert data["agents"]["own"]["score"] is None
    assert data["agents"]["own"]["provisional_score"] is None


def test_mutated_patch_or_report_invalidates_grade(tmp_path):
    experiment, rows, record = fixture(tmp_path)
    report = official(tmp_path, record)
    report.write_text(report.read_text() + " ")
    data = results(tmp_path, experiment, rows, "completed", "resolved")
    assert data["states"]["scoring"] == "failed"
    assert "hash differs" in data["issues"][0]
    official(tmp_path, record)
    (tmp_path / "jobs/own/first/verifier/model.patch").write_text("different patch")
    assert "fingerprint differs" in results(tmp_path, experiment, rows, "completed", "resolved")["issues"][0]


def test_generic_resolved_metric_does_not_select_swe(tmp_path):
    experiment, rows, _ = fixture(tmp_path)
    experiment["benchmark"]["dataset"] = {"name": "custom/tool-use"}
    data = results(tmp_path, experiment, rows, "completed", "resolved")
    assert data["source"] == "harbor"
    assert data["agents"]["own"]["score"] == 1


def test_assistance_and_cleanup_failure_are_not_dropped(tmp_path):
    experiment, rows, record = fixture(tmp_path)
    rows[0]["guardian"] = {"assisted": True}
    official(tmp_path, record, resolved=1)
    write(tmp_path / "jobs/own/first/retention.json", {"cleanup_failed": True})
    data = results(tmp_path, experiment, rows, "completed", "resolved")
    assert data["agents"]["own"]["measurement_mode"] == "assisted"
    assert data["agents"]["own"]["recorded"] == 1
    assert data["states"]["resources"] == "cleanup_failed"


def test_agent_failure_does_not_hide_completed_official_grading(tmp_path):
    experiment, rows, record = fixture(tmp_path)
    rows[0]["exception"] = {"exception_type": "AgentTimeoutError"}
    official(tmp_path, record, resolved=0)
    data = results(tmp_path, experiment, rows, "failed", "resolved")
    assert data["states"]["execution"] == "failed"
    assert data["states"]["scoring"] == "complete"
    assert data["agents"]["own"]["graded"] == 1
    assert data["agents"]["own"]["errors"] == 1
    assert data["agents"]["own"]["score"] is None
