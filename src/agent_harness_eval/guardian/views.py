"""Bounded public incident views. Raw snapshots stay in the local evidence store."""

import hashlib
import json

from .evidence import incident_summary, read


def incidents(root):
    from ..console import safe_file

    rows = []
    for trial in sorted((root / "jobs").glob("*/*")):
        if not trial.is_dir() or trial.name.startswith("."):
            continue
        try:
            safe_file(root, trial)
        except ValueError:
            continue
        summary = incident_summary(trial)
        for item in summary["incidents"]:
            directory = trial / "incidents" / item["id"]
            try:
                safe_file(root, directory)
            except ValueError:
                continue
            identity = hashlib.sha256(
                str(directory.relative_to(root)).encode()
            ).hexdigest()[:24]
            rows.append(
                {
                    **item,
                    "id": identity,
                    "trial": trial.name,
                    "agent": trial.parent.name,
                    "directory": str(directory.relative_to(root)),
                    "outcome": {
                        k: item["outcome"].get(k)
                        for k in ("status", "finished_at", "phase")
                    },
                }
            )
            if len(rows) >= 1000:
                return {"incidents": rows, "truncated": True}
    return {"incidents": rows, "truncated": False}


def detail(root, identity):
    from ..console import redact, safe_file

    row = next((r for r in incidents(root)["incidents"] if r["id"] == identity), None)
    if row is None:
        raise KeyError(identity)
    directory = safe_file(root, root / row["directory"])
    actions = []
    path = safe_file(root, directory / "actions.jsonl")
    if path.exists():
        with path.open(encoding="utf-8") as f:
            scanned = 0
            for _ in range(250):
                line = f.readline(131073)
                scanned += len(line)
                if not line:
                    break
                if len(line) > 131072:
                    # Skip a large raw snapshot without hiding later command records.
                    while not line.endswith("\n") and scanned < 4 * 1024 * 1024:
                        line = f.readline(131073)
                        scanned += len(line)
                        if not line:
                            break
                    if scanned >= 4 * 1024 * 1024:
                        break
                    continue
                try:
                    action = json.loads(line)
                    args = action.get("arguments") or {}
                    result = action.get("result") or {}
                    actions.append(
                        {
                            "time": action.get("time"),
                            "kind": action.get("kind"),
                            "id": action.get("id"),
                            "tool": action.get("tool", action.get("action")),
                            "command": redact(str(args.get("command", ""))[:4000]),
                            "returncode": result.get("returncode"),
                            "error": redact(str(result.get("error", ""))[:1000]),
                        }
                    )
                except (ValueError, TypeError, AttributeError):
                    continue
    diagnosis = read(safe_file(root, directory / "diagnosis.json"), {}) or {}
    return {
        **row,
        "actions": actions,
        "summary": redact(str(diagnosis.get("summary", ""))[:6000]),
        "note": "原始快照与工具输出保存在实验目录；此处不展示原始模型推理和日志正文。",
    }
