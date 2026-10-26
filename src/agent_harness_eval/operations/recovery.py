"""Conservative Linux process reconciliation; PID alone is not identity."""

from pathlib import Path


def start_ticks(pid):
    try:
        return Path(f"/proc/{int(pid)}/stat").read_text().rsplit(")", 1)[1].split()[19]
    except (OSError, ValueError, IndexError, TypeError):
        return None


def reconcile(commands):
    results = []
    with commands.store.transaction() as db:
        rows = db.execute("SELECT * FROM jobs WHERE status IN ('running','pausing','paused','cancelling')").fetchall()
        for row in rows:
            observed = start_ticks(row["pid"])
            alive = observed is not None and observed == row["start_ticks"]
            reason = "worker_still_alive" if alive else "external_outcome_unknown"
            # Even a dead process may have submitted an external request or left containers.
            # Increment the fence before accepting any future worker callback.
            db.execute("UPDATE jobs SET status='interrupted',generation=generation+1,error=? WHERE id=?",
                       (reason, row["id"]))
            commands.store.event(db, "job.reconciliation_required", row["id"], {"reason": reason})
            results.append({"id": row["id"], "reason": reason})
    return results
