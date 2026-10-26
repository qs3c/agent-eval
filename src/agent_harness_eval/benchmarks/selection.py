"""Stable subset selection across runtimes; content changes invalidate receipts."""

import hashlib
import json
from pathlib import Path

from ..freezing import freeze_tree
from ..provenance import tree_manifest, write_json
from ..workspace.models import identity


def inventory(paths):
    result = {}
    hash_cache = {}
    for value in paths:
        path = Path(value)
        if path.name in result:
            raise ValueError("Duplicate task directory name")
        if path.is_symlink() or not (path / "task.toml").is_file():
            raise ValueError("Expected a regular Harbor task directory")
        result[path.name] = tree_manifest(path, hash_cache=hash_cache)["sha256"]
    return result


def select(paths, *, count, seed, excluded=(), dataset=None):
    hashes = inventory(paths)
    if type(seed) is not int or type(count) is not int or count <= 0:
        raise ValueError("Integer seed and positive count required")
    excluded = sorted(set(excluded))
    eligible = set(hashes) - set(excluded)
    if count > len(eligible):
        raise ValueError("Requested count exceeds eligible tasks")
    selected = sorted(eligible, key=lambda name: (hashlib.sha256(f"{seed}\0{name}".encode()).hexdigest(), name))[:count]
    value = {"schema_version": 1, "algorithm": "sha256-seed-task-rank-v1", "seed": seed,
             "dataset": dataset, "excluded": excluded, "task_ids": selected, "candidate_hashes": hashes}
    return {**value, "selection_id": identity(value)}


def materialize(paths, selection, output):
    output = Path(output)
    expected = {key: value for key, value in selection.items() if key != "selection_id"}
    if identity(expected) != selection["selection_id"] or inventory(paths) != selection["candidate_hashes"]:
        raise ValueError("Selection input or task content changed")
    originals = {Path(path).name: Path(path) for path in paths}
    if any(output.resolve().is_relative_to(path.resolve()) for path in originals.values()):
        raise ValueError("Selection output must be outside task inputs")
    output.mkdir(parents=True, exist_ok=False)
    result = []
    for name in selection["task_ids"]:
        target = output / name
        freeze_tree(originals[name], target, output / "shared-runtime")
        result.append(target)
    write_json(output / "selection.json", selection)
    return result


def historical_tasks(runs, *, benchmark_id):
    from ..manifests import load_manifest

    selected = set()
    for run in runs:
        manifest = load_manifest(Path(run))
        benchmark = manifest["experiment"]["benchmark"]
        if benchmark["id"] != benchmark_id:
            raise ValueError("Excluded history belongs to another benchmark")
        selected.update(Path(task).name for task in benchmark["tasks"])
    return selected
