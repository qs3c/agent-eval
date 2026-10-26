"""Select an explicit scorer without replacing the original Harbor observations."""

import hashlib
import json
from collections import defaultdict
from copy import deepcopy
from pathlib import Path

from ..comparison import summarize
from ..provenance import digest


def read(path, default=None):
    try:
        value = json.loads(path.read_text())
        return value if isinstance(value, dict) else default
    except (OSError, ValueError):
        return default


def safe(output, name):
    path = output / name
    if path.is_symlink() or not path.resolve().is_relative_to(output.resolve()):
        raise ValueError("Grading evidence escapes experiment")
    return path


def uses_official_swe(experiment, output):
    benchmark = experiment["benchmark"]
    if benchmark.get("plugin"):
        return benchmark["plugin"] == "swe"
    dataset = (benchmark.get("dataset") or {}).get("name", "")
    if isinstance(dataset, str) and "swe-bench" in dataset.lower():
        return True
    original = (read(output / "manifest.json", {}) or {}).get("experiment", {}).get("benchmark", {})
    if original.get("score_kind") == "metric":
        return False
    # Legacy experiments require recorded provenance, never a generic resolved metric.
    return bool(benchmark["tasks"]) and all(
        (read(output / "tasks" / Path(task).name / "swe-provenance.json", {}) or {}).get("instance_id")
        for task in benchmark["tasks"])


def official_trials(output, experiment, trials):
    from ..regrading import EVALUATOR, valid_report

    projected = deepcopy(trials)
    grouped, units = defaultdict(list), defaultdict(list)
    issues = []
    for row in projected:
        row["harbor_metrics"] = row.get("metrics", {})
        row["metrics"] = {}
        row["grading_status"] = "pending"
        grouped[row.get("agent"), row.get("task")].append(row)
    for path in (output / "batches").glob("*/official/progress.json"):
        try:
            progress = read(safe(output, str(path.relative_to(output))))
        except ValueError:
            progress = None
        if not isinstance(progress, dict) or not isinstance(progress.get("units", {}), dict):
            issues.append("Invalid grading progress: " + str(path.relative_to(output)))
            continue
        for unit in progress.get("units", {}).values():
            if (not isinstance(unit, dict) or not isinstance(unit.get("agent"), str)
                    or not isinstance(unit.get("task"), str) or type(unit.get("attempt")) is not int
                    or unit["attempt"] < 1):
                issues.append("Invalid official grading unit")
                continue
            units[unit["agent"], unit["task"], unit["attempt"]].append((progress.get("evaluator"), unit))
    for (agent, task), rows in grouped.items():
        def order(row):
            try:
                path = safe(output, row["result"])
                return (read(path, {}).get("started_at", ""), str(path))
            except (KeyError, ValueError):
                return ("", "")
        rows.sort(key=order)
        for ordinal, row in enumerate(rows, 1):
            matching = units[agent, task, ordinal]
            if not matching:
                continue
            if len(matching) != 1:
                row["grading_status"] = "ambiguous"
                issues.append(f"Duplicate official grade: {agent}/{task}/{ordinal}")
                continue
            evaluator, unit = matching[0]
            row["grading_status"] = unit.get("status", "pending")
            if unit.get("status") != "completed":
                continue
            try:
                if evaluator != EVALUATOR:
                    raise ValueError("Scorer version differs")
                result = safe(output, row["result"])
                patch = result.parent / "verifier/model.patch"
                if patch.is_symlink() or not patch.is_file():
                    raise ValueError("Captured patch is missing")
                record = read(safe(output, f"tasks/{task}/tests/config.json"))
                prediction = {"instance_id": task, "model_patch": patch.read_text(), "model_name_or_path": agent}
                fingerprint = hashlib.sha256(json.dumps([record, prediction, EVALUATOR], sort_keys=True).encode()).hexdigest()
                if fingerprint != unit.get("input_sha256"):
                    raise ValueError("Grading input fingerprint differs")
                report = safe(output, unit["report"])
                if digest(report) != unit.get("report_sha256"):
                    raise ValueError("Grading report hash differs")
                _, value = valid_report(report.parent, {task})
                row["metrics"] = {experiment["benchmark"]["primary_metric"]: value["resolved_instances"]}
                row["scorer"] = EVALUATOR
                row["grade_report"] = unit["report"]
                row["grade_completed"] = True
            except (OSError, ValueError, KeyError, TypeError) as exc:
                row["grading_status"] = "invalid_evidence"
                issues.append(f"{agent}/{task}/{ordinal}: {exc}")
    return projected, issues


def results(output, experiment, trials, execution, score_kind):
    output = Path(output)
    official = uses_official_swe(experiment, output)
    selected, issues = official_trials(output, experiment, trials) if official else (deepcopy(trials), [])
    terminal = execution in {"completed", "failed", "interrupted", "cancelled", "incomplete"}
    data = summarize(experiment, selected, "resolved" if official else score_kind, terminal)
    from ..policies.assurance import assess

    data["model_assurance"] = assess(output, experiment)
    expected = len(experiment["agents"]) * len(experiment["benchmark"]["tasks"]) * experiment["attempts"]
    def graded(row):
        from ..comparison import numeric

        value = (row.get("metrics") or {}).get(experiment["benchmark"]["primary_metric"])
        return bool(row.get("grade_completed")) if official else numeric(value) and (score_kind != "resolved" or value in (0, 1))
    scored = sum(graded(row) for row in selected)
    complete_evidence = (len(trials) == expected and not data["unexpected"]
                         and all(not agent["missing"] and not agent["extra"] for agent in data["agents"].values()))
    if scored == expected and not issues and complete_evidence:
        scoring = "complete"
    elif scored:
        scoring = "partial"
    elif issues or any(row.get("grading_status") in {"failed", "invalid_evidence", "ambiguous"} for row in selected):
        scoring = "failed"
    elif any(row.get("grading_status") in {"running", "retrying"} for row in selected):
        scoring = "running"
    else:
        scoring = "pending"
    cleanup = [read(path, {}) for path in (output / "batches").glob("*/cleanup.json")]
    retained = [read(path, {}) for path in (output / "jobs").glob("*/*/retention.json")]
    if any(value.get("cleanup_failed") for value in retained) or any(value.get("failed_removals") for value in cleanup):
        resources = "cleanup_failed"
    elif any(value.get("status") == "snapshot_retained" for value in retained) or any(
        value.get("status", "").startswith(("retained", "deferred")) for value in cleanup):
        resources = "retained"
    elif not terminal:
        resources = "active"
    elif complete_evidence:
        resources = "reclaimed"
    else:
        resources = "reclaim_pending"
    state = "running" if execution == "pausing" else "paused" if execution == "held" else "interrupted" if execution == "incomplete" else execution
    data.update(source="official_swe" if official else "harbor", issues=issues,
        states={"execution": state, "scoring": scoring,
                "evidence": "missing" if not trials else "complete" if complete_evidence else "partial",
                "resources": resources},
        denominators={"planned": expected, "executed": len(trials), "scored": scored},
        harbor=summarize(experiment, trials, score_kind, terminal) if official else None)
    for agent_id, agent in data["agents"].items():
        agent["graded"] = sum(graded(row) for row in selected if row.get("agent") == agent_id)
        for task, cells in data["tasks"].items():
            cells[agent_id]["graded"] = sum(graded(row) for row in selected if row.get("agent") == agent_id and row.get("task") == task)
        # Keep the numerator visible; never format a partial observed count as a total success rate.
        agent["provisional_score"] = None
        agent["measurement_mode"] = "assisted" if agent["assisted"] else "autonomous"
        usage = agent["usage"]["cost_usd"]
        usage["unknown_count"] = max(0, usage["expected"] - usage["recorded"])
        usage["unknown_fraction"] = usage["unknown_count"] / usage["expected"] if usage["expected"] else None
    return data
