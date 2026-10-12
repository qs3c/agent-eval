"""Immutable initial snapshots; summaries never replace original evidence."""

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4


def now():
    return datetime.now(UTC).isoformat()


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp-" + uuid4().hex)
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(temporary, path)


def read(path, default=None):
    try:
        path = Path(path)
        if path.is_symlink() or path.stat().st_size > 4 * 1024 * 1024:
            return default
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def append(path, value):
    with Path(path).open("a", encoding="utf-8") as f:
        f.write(json.dumps({**value, "time": now()}, ensure_ascii=False) + "\n")
        f.flush()


def log_tail(path, limit=64 * 1024):
    try:
        if path.is_symlink():
            return {"error": "symlink not followed"}
        with path.open("rb") as f:
            size = os.fstat(f.fileno()).st_size
            start = max(0, size - limit)
            f.seek(start)
            data = f.read(limit)
        return {
            "offset": start,
            "observed_size": size,
            "truncated": start > 0,
            "sha256": hashlib.sha256(data).hexdigest(),
            "text": data.decode("utf-8", "replace"),
        }
    except OSError as error:
        return {"error": str(error)}


async def capture(root, backend, reason):
    root.mkdir(parents=True, exist_ok=False)
    write(root / "incident.json", {"schema_version": 1, "created_at": now(), **reason})
    # Host copies survive Docker failure/deletion and establish evidence provenance.
    trial = root.parent.parent
    write(
        root / "host.json",
        {
            "captured_at": now(),
            "source": "trial_artifacts",
            "files": {
                str(p.relative_to(trial)): log_tail(p)
                for p in [
                    trial / "guardian-state.json",
                    trial / "guardian-worker.log",
                    trial / "agent/events.jsonl",
                    trial / "agent/stderr.log",
                ]
            },
        },
    )
    try:
        snapshot = await backend.snapshot()
    except Exception as error:  # noqa: BLE001 - preserve evidence and continue cleanup on adapter failure
        snapshot = {"error": str(error), "captured_at": now()}
    write(root / "initial.json", snapshot)
    return snapshot


def incident_summary(trial):
    records = []
    for path in sorted((trial / "incidents").glob("*/incident.json")):
        if (
            not path.resolve().is_relative_to(trial.resolve())
            or path.parent.is_symlink()
        ):
            continue
        datum = read(path, {})
        if not isinstance(datum, dict):
            continue
        outcome = read(path.parent / "outcome.json", {})
        records.append(
            {
                "id": path.parent.name,
                "reason": datum.get("reason"),
                "created_at": datum.get("created_at"),
                "assisted": (path.parent / "assisted.json").is_file(),
                "outcome": outcome if isinstance(outcome, dict) else {},
            }
        )
    legacy = read(trial / "human-intervention.json", {})
    return {
        "assisted": any(r["assisted"] for r in records)
        or bool(isinstance(legacy, dict) and legacy.get("human_intervened")),
        "incidents": records,
    }
