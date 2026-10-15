"""Durable conservative leases: failed grading keeps images until recovery.

Collection waits until all registered batches are ready. This deliberately
prefers retaining shared images to guessing whether another batch needs them.
"""

import hashlib
import json
from pathlib import Path

from .guardian.evidence import write


def registry():
    from .regrading import state_root

    return state_root() / "image-leases.json"


def identity(output, number):
    return hashlib.sha256(f"{output.resolve()}:{number}".encode()).hexdigest()


def leases():
    path = registry()
    if not path.exists():
        return {}
    value = json.loads(path.read_text())
    if not isinstance(value, dict) or any(
        not isinstance(v, dict) or v.get("status") not in {"ready", "waiting"}
        for v in value.values()
    ):
        raise ValueError("镜像租约损坏，拒绝清理")
    return value


def retain(output, paths, number):
    from .regrading import gate

    with gate("image-lifecycle"):
        entries = leases()
        entries[identity(output, number)] = {
            "output": str(output.resolve()),
            "paths": [str(p.resolve()) for p in paths],
            "number": number,
            "status": "waiting",
        }
        write(registry(), entries)


def collect(exp, output, paths, number):
    from .batching import _cleanup_batch_images
    from .regrading import gate

    # New batches must register before preparing/using images. Holding this
    # lock through collection prevents a new consumer from racing deletion.
    with gate("image-lifecycle"):
        entries = leases()
        key = identity(output, number)
        entries[key] = {
            "output": str(output.resolve()),
            "paths": [str(p.resolve()) for p in paths],
            "number": number,
            "status": "ready",
        }
        write(registry(), entries)
        if any(v["status"] == "waiting" for v in entries.values()):
            write(output / "batches" / f"{number:03d}" / "cleanup.json", {
                "status": "deferred_active_or_pending_grading",
                "note": "其他批次仍在执行或等待复评，保留镜像。",
            })
            return
        # Includes standalone CLI grading, whose child inherits the shared lock.
        with gate("image-use"):
            for lease_key, lease in list(entries.items()):
                root = Path(lease["output"])
                if not root.is_dir():
                    # A missing archive cannot authorize deleting its resources.
                    continue
                completed = _cleanup_batch_images(
                    exp, root, [Path(p) for p in lease["paths"]], lease["number"]
                )
                if completed is False:
                    continue
                del entries[lease_key]
                write(registry(), entries)
