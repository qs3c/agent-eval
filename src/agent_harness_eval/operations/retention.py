"""GC selects only owned assets without consumers; deletion requires fresh proof."""

import time
import json
import subprocess


def collect(resources, verify_and_delete, *, now=None, limit=32):
    from ..workspace.migrations import exclusive

    with exclusive(resources.directory / "gc.lock"):
        with resources.transaction() as db:
            db.execute("UPDATE assets SET state='cleanup_failed',error='Previous collector interrupted; re-inspect actual resource' WHERE state='deleting'")
        return _collect(resources, verify_and_delete, now=now, limit=limit)


def _collect(resources, verify_and_delete, *, now=None, limit=32):
    stamp = time.time() if now is None else now
    with resources.transaction() as db:
        rows = db.execute("SELECT * FROM assets a WHERE owned=1 AND state IN ('retained','cleanup_failed') "
            "AND expires IS NOT NULL AND expires<=? AND NOT EXISTS(SELECT 1 FROM consumers c WHERE c.asset=a.id) "
            "ORDER BY expires,id LIMIT ?", (stamp, limit)).fetchall()
        for row in rows:
            db.execute("UPDATE assets SET state='deleting' WHERE id=? AND generation=?", (row["id"], row["generation"]))
    results = []
    for row in rows:
        try:
            # Executor must re-inspect labels/identity and external references, not trust a name.
            removed = verify_and_delete(dict(row))
            state, error = ("deleted", None) if removed else ("retained", "resource still referenced or ownership unconfirmed")
        except Exception as exc:
            state, error = "cleanup_failed", str(exc)
        with resources.transaction() as db:
            changed = db.execute("UPDATE assets SET state=?,error=? WHERE id=? AND generation=? AND state='deleting'",
                                 (state, error, row["id"], row["generation"])).rowcount
            if changed != 1:
                raise ValueError("Asset reclamation fence changed")
        results.append({"id": row["id"], "state": state, "error": error})
    return results


def delete_stopped_container(asset):
    """Only the recorded ID with the recorded immutable Compose label may be removed."""
    if asset["kind"] != "container" or not asset["id"].startswith("container:"):
        return False
    cid = asset["id"].removeprefix("container:")
    import re

    if not re.fullmatch(r"[0-9a-f]{64}", cid):
        return False
    result = subprocess.run(["docker", "inspect", "--format",
        "{{json .Id}}\n{{json .Config.Labels}}\n{{json .State.Running}}", cid],
        capture_output=True, text=True, timeout=5, check=False)
    if result.returncode:
        if "no such object" in result.stderr.lower() or "no such container" in result.stderr.lower():
            return True
        raise RuntimeError("Cannot inspect retained container")
    actual, labels, running = map(json.loads, result.stdout.splitlines())
    if actual != cid or labels.get("com.docker.compose.project") != asset["owner"] or running:
        return False
    removed = subprocess.run(["docker", "rm", cid], capture_output=True, text=True, timeout=5, check=False)
    if removed.returncode:
        raise RuntimeError("Retained container removal failed")
    return True
