"""Keep failures and unknown fields visible; never blend benchmark metrics."""

import json
import math
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from .guardian.evidence import incident_summary
from .manifests import load_manifest
from .provenance import write_json


def duration(value):
    try:
        return (
            datetime.fromisoformat(value["finished_at"])
            - datetime.fromisoformat(value["started_at"])
        ).total_seconds()
    except (KeyError, TypeError, ValueError):
        return None


def task_key(value):
    """Harbor's display name may differ from its task directory identity."""
    path = (value.get("task_id") or {}).get("path")
    return Path(path).name if path else value.get("task_name")


def report(root: Path) -> dict:
    manifest = load_manifest(root)
    exp = manifest["experiment"]
    metric = exp["benchmark"]["primary_metric"]
    trials = []
    report_errors = []
    for path in sorted((root / "jobs").glob("*/*/result.json")):
        try:
            value = json.loads(path.read_text())
            if not isinstance(value, dict):
                raise ValueError("result must be an object")  # noqa: TRY004 - malformed JSON is a data validation failure
            rewards = (value.get("verifier_result") or {}).get("rewards") or {}
            if not isinstance(rewards, dict) or any(
                not isinstance(v, (int, float)) or not math.isfinite(v)
                for v in rewards.values()
            ):
                raise ValueError("rewards must contain finite numeric values")
        except (ValueError, TypeError, AttributeError) as error:
            report_errors.append(
                {"result": str(path.relative_to(root)), "error": str(error)}
            )
            continue
        if "task_name" not in value:
            continue
        exception = value.get("exception_info")
        verifier = value.get("verifier_result") or {}
        usage = value.get("agent_result") or {}
        trials.append(
            {
                "agent": path.parent.parent.name,
                "task": task_key(value),
                "task_name": value["task_name"],
                "trial": value["trial_name"],
                "exception": exception,
                "metrics": verifier.get("rewards") or {},
                "input_tokens": usage.get("n_input_tokens"),
                "output_tokens": usage.get("n_output_tokens"),
                "cost_usd": usage.get("cost_usd"),
                "agent_seconds": duration(value.get("agent_execution") or {}),
                "trial_seconds": duration(value),
                "result": str(path.relative_to(root)),
                "guardian": incident_summary(path.parent),
            }
        )
    data = summarize(manifest, trials, report_errors)
    summaries = data["agents"]
    write_json(root / "report.json", data)
    lines = [
        f"# {exp['name']}",
        "",
        manifest["evidence_kind"],
        "",
        f"Benchmark: {data['benchmark']}; metric: {metric}",
        "",
        "| Agent | Expected | Recorded | Errors | Scored | Mean on scored | Missing |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for name, s in summaries.items():
        lines.append(
            f"| {name} | {s['expected']} | {s['recorded']} | {s['errors']} | {s['scored']} | {s['mean_on_scored']} | {s['missing']} |"
        )
    for name, s in summaries.items():
        if s.get("assisted"):
            lines += [
                "",
                f"{name}: {s['assisted']} externally assisted attempts. Raw scores are retained; these are not autonomous results.",
            ]
    lines += [
        "",
        "Unknown cost remains null. Missing/errored trials are explicitly counted; JSON also reports a diagnostic mean treating unscored attempts as zero. Zero is only a lower bound for nonnegative metrics.",
        "Paired task values require all requested attempts to be scored. No significance claim is made from smoke tests.",
    ]
    if report_errors:
        lines.append(
            f"Malformed result files: {len(report_errors)}; counted as missing, with details in report_errors."
        )
    (root / "report.md").write_text("\n".join(lines) + "\n")
    return data


def summarize(manifest, trials, report_errors=None):
    """Build a report without writing or rereading evidence files."""
    exp = manifest["experiment"]
    metric = exp["benchmark"]["primary_metric"]
    expected = len(exp["benchmark"]["tasks"]) * exp["attempts"]
    summaries = {}
    for agent in exp["agents"]:
        rows = [t for t in trials if t["agent"] == agent["id"]]
        scores = [
            t["metrics"][metric]
            for t in rows
            if metric in t["metrics"] and not t["exception"]
        ]
        summaries[agent["id"]] = {
            "expected": expected,
            "recorded": len(rows),
            "missing": expected - len(rows),
            "errors": sum(t["exception"] is not None for t in rows),
            "scored": len(scores),
            "mean_on_scored": sum(scores) / len(scores) if scores else None,
            "mean_with_unscored_zero": sum(scores) / expected,
            "cost_usd": sum(t["cost_usd"] for t in rows)
            if len(rows) == expected and all(t["cost_usd"] is not None for t in rows)
            else None,
        }
        assisted = sum(t["guardian"]["assisted"] for t in rows)
        if assisted:
            summaries[agent["id"]]["assisted"] = assisted
            summaries[agent["id"]]["score_note"] = (
                "原始评分含外部辅助尝试，不代表纯自主成绩"
            )
    by_task = defaultdict(dict)
    for a in summaries:
        for task in manifest["tasks"]:
            rows = [t for t in trials if t["agent"] == a and t["task"] == task]
            scores = [r["metrics"].get(metric) for r in rows if not r["exception"]]
            by_task[task][a] = (
                sum(scores) / len(scores)
                if len(scores) == exp["attempts"] and all(s is not None for s in scores)
                else None
            )
    data = {
        "benchmark": exp["benchmark"]["id"],
        "primary_metric": metric,
        "evidence_kind": manifest["evidence_kind"],
        "agents": summaries,
        "paired_tasks": dict(by_task),
        "trials": trials,
        "report_errors": report_errors or [],
    }
    return data
