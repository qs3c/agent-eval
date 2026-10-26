"""Compact run manifests with content-addressed audit attachments.

Task digests retain the v1 definition. Historic manifests are never rewritten;
their derived index is invalidated by a change to the source file's stat identity.
"""

import hashlib
import json
import os
import tempfile
from collections import defaultdict
from pathlib import Path

from .provenance import digest


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".manifest-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def file_identity(path):
    stat = path.stat()
    return [stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns]


def store_inventory(root, files):
    raw = json.dumps(files, sort_keys=True, ensure_ascii=True).encode()
    sha = hashlib.sha256(raw).hexdigest()
    path = root / "provenance" / "inventories" / f"{sha}.json"
    if not path.exists():
        atomic_json(path, files)
    elif json.loads(path.read_text()) != files:
        raise ValueError(f"Conflicting inventory: {path}")
    return {
        "path": str(path.relative_to(root)),
        "sha256": sha,
        "file_count": len(files),
    }


def compact_manifest(root, manifest):
    result = {**manifest, "manifest_schema_version": 2, "tasks": {}}
    for task, entry in manifest.get("tasks", {}).items():
        if "files" not in entry:
            result["tasks"][task] = entry
            continue
        groups = defaultdict(dict)
        for name, sha in entry["files"].items():
            # Separate bundled dependencies from per-task inputs. Equal inventories
            # across tasks share one attachment, including legacy unpacked runtimes.
            group = "runtime" if name.startswith("environment/grader/") else "inputs"
            groups[group][name] = sha
        result["tasks"][task] = {
            **{k: v for k, v in entry.items() if k != "files"},
            "file_count": len(entry["files"]),
            "inventories": {
                k: store_inventory(root, files) for k, files in groups.items()
            },
        }
    workbench = result.get("workbench", {})
    if "files" in workbench:
        result["workbench"] = {
            **{k: v for k, v in workbench.items() if k != "files"},
            "inventory": store_inventory(root, workbench["files"]),
        }
    return result


def index_legacy_manifest(root):
    root = Path(root)
    path = root / "manifest.json"
    before = file_identity(path)
    if path.is_symlink():
        raise ValueError("Manifest must not be a symlink")
    manifest = json.loads(path.read_text())
    if manifest.get("manifest_schema_version") == 2:
        return path
    compact = compact_manifest(root, manifest)
    source_sha = digest(path)
    if file_identity(path) != before:
        raise ValueError(
            "Manifest changed during indexing; retry after the run finishes"
        )
    index = root / "manifest.index.json"
    atomic_json(
        index,
        {"source_identity": before, "source_sha256": source_sha, "manifest": compact},
    )
    return index


def load_manifest(root):
    root = Path(root)
    source = root / "manifest.json"
    if source.is_symlink():
        raise ValueError("Manifest must not be a symlink")
    index = root / "manifest.index.json"
    if index.is_file() and not index.is_symlink():
        try:
            if index.stat().st_size <= 16 * 1024 * 1024:
                value = json.loads(index.read_text())
                if value["source_identity"] == file_identity(source) and isinstance(
                    value["manifest"], dict
                ):
                    return value["manifest"]
        except (KeyError, TypeError, ValueError):
            pass  # Invalid or stale index falls back to the authoritative source.
    value = json.loads(source.read_text())
    if not isinstance(value, dict):
        raise TypeError("Manifest must contain an object")
    return value


def index_plan(root, store, *, idempotency_key):
    from .freezing import plan_from_manifest

    path = Path(root) / "manifest.json"
    plan = plan_from_manifest(load_manifest(root), evidence=(str(path.resolve()),))
    return store.create_plan(plan, idempotency_key)


def verify_manifest(root):
    """Explicit audit: check the legacy source, attachments and v1 task digests."""
    root = Path(root)
    value = load_manifest(root)
    index = root / "manifest.index.json"
    if index.exists():
        indexed = json.loads(index.read_text())
        if indexed["source_sha256"] != digest(root / "manifest.json"):
            raise ValueError("Legacy manifest hash mismatch")
    def read_inventory(ref):
        path = root / ref["path"]
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
            raise ValueError("Inventory path escapes run")
        inventory = json.loads(path.read_text())
        raw = json.dumps(inventory, sort_keys=True, ensure_ascii=True).encode()
        if hashlib.sha256(raw).hexdigest() != ref["sha256"] or len(inventory) != ref["file_count"]:
            raise ValueError("Inventory hash mismatch")
        return inventory

    workbench = value.get("workbench", {})
    if "inventory" in workbench:
        read_inventory(workbench["inventory"])
    for task in value.get("tasks", {}).values():
        if "inventories" not in task:
            continue
        files = {}
        for ref in task["inventories"].values():
            inventory = read_inventory(ref)
            if files.keys() & inventory.keys():
                raise ValueError("Duplicate inventory paths")
            files.update(inventory)
        sha = hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()
        if sha != task["sha256"]:
            raise ValueError("Task inventory hash mismatch")
    return value
