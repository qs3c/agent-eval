"""Task-balanced comparisons over the configured sample, without hiding missing work."""

import math
from collections import defaultdict
from pathlib import Path


def numeric(value):
    return type(value) in (int, float) and math.isfinite(value)


def summarize(experiment, trials, score_kind, terminal):
    benchmark = experiment["benchmark"]
    tasks = [Path(p).name for p in benchmark["tasks"]]
    agents = [a["id"] for a in experiment["agents"]]
    attempts = experiment["attempts"]
    expected = len(tasks) * attempts
    metric = benchmark["primary_metric"]
    grouped = defaultdict(list)
    unexpected = []
    for row in trials:
        if row.get("agent") not in agents or row.get("task") not in tasks:
            unexpected.append({"agent": row.get("agent"), "task": row.get("task")})
            continue
        grouped[row["agent"], row["task"]].append(row)

    summaries, matrix = {}, {task: {} for task in tasks}
    for agent in agents:
        rows = []
        cells = []
        for task in tasks:
            records = grouped[agent, task]
            rows.extend(records)
            values = []
            for row in records:
                value = (row.get("metrics") or {}).get(metric)
                if (
                    row.get("exception") is None
                    and numeric(value)
                    and (score_kind != "resolved" or value in (0, 1))
                ):
                    values.append(value)
            complete = len(records) == attempts and len(values) == attempts
            cell = {
                "assisted": sum(
                    bool(r.get("guardian", {}).get("assisted")) for r in records
                ),
                "recorded": len(records),
                "expected": attempts,
                "missing": max(0, attempts - len(records)),
                "extra": max(0, len(records) - attempts),
                "errors": sum(r.get("exception") is not None for r in records),
                "scored": len(values),
                "unscored": len(records) - len(values),
                "successes": sum(v == 1 for v in values)
                if score_kind == "resolved"
                else None,
                "score": sum(values) / attempts if complete else None,
                "trials": [
                    {k: r.get(k) for k in ("id", "trial", "exception", "metrics")}
                    for r in records
                ],
            }
            cells.append(cell)
            matrix[task][agent] = cell
        counts = {
            key: sum(c[key] for c in cells)
            for key in ("missing", "extra", "errors", "scored", "unscored")
        }
        complete = all(c["score"] is not None for c in cells) and not unexpected
        final = complete and terminal
        successes = (
            sum(c["successes"] for c in cells) if score_kind == "resolved" else None
        )
        usage = {}
        for field in ("input_tokens", "output_tokens", "agent_seconds", "cost_usd"):
            values = [r[field] for r in rows if numeric(r.get(field)) and r[field] >= 0]
            usage[field] = {
                "total": sum(values)
                if len(values) == expected
                and len(rows) == expected
                and not counts["extra"]
                and not unexpected
                else None,
                "known": sum(values) if values else None,
                "recorded": len(values),
                "expected": expected,
            }
        summaries[agent] = {
            "assisted": sum(c["assisted"] for c in cells),
            **counts,
            "recorded": len(rows),
            "expected": expected,
            "complete": complete,
            "final": final,
            "score": sum(c["score"] for c in cells) / len(tasks) if final else None,
            # Observed successes / planned attempts is provisional, not a missing=failed score.
            "provisional_score": successes / expected
            if successes is not None and not counts["extra"] and not unexpected
            else None,
            "successes": successes,
            "solved_tasks": sum(c["score"] == 1 for c in cells)
            if score_kind == "resolved"
            else None,
            "usage": usage,
        }
    return {
        "score_kind": score_kind,
        "primary_metric": metric,
        "attempts": attempts,
        "task_count": len(tasks),
        "agents": summaries,
        "tasks": matrix,
        "unexpected": unexpected,
    }
