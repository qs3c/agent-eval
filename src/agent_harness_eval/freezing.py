"""Independent frozen trees with one owned copy of each shared grader archive."""

import os
import shutil
from pathlib import Path

from .provenance import digest


def selection_record(tasks):
    if not tasks:
        return None
    source = Path(tasks[0]).parent / "selection.json"
    if source.is_symlink():
        raise ValueError("Selection must not be a symlink")
    if source.is_file():
        return {"path": "selection.json", "sha256": digest(source)}
    return None


def freeze_selection(tasks, target):
    if selection_record(tasks):
        target.mkdir(parents=True, exist_ok=True)
        shutil.copy2(Path(tasks[0]).parent / "selection.json", target / "selection.json")


def plan_from_manifest(manifest, *, evidence=()):
    """Index recorded inputs without inventing source revisions for legacy runs."""
    from .workspace.models import AgentRelease, BenchmarkRevision, ExperimentPlan

    exp = manifest["experiment"]
    benchmark = exp["benchmark"]
    return ExperimentPlan(
        experiment=exp,
        agents=tuple(AgentRelease(
            agent_id=agent["id"],
            artifact_digests=manifest.get("agent_artifacts", {}).get(agent["id"], {}),
        ) for agent in exp["agents"]),
        benchmark=BenchmarkRevision(
            benchmark_id=benchmark["id"],
            task_ids=tuple(Path(p).name for p in benchmark["tasks"]),
            task_hashes={name: entry["sha256"] for name, entry in manifest.get("tasks", {}).items()
                         if entry.get("sha256")},
            dataset=benchmark.get("dataset"), scorer=benchmark.get("evaluation"),
            selection=manifest.get("selection"),
        ),
        workbench=manifest.get("workbench", {}), evidence=tuple(evidence),
    )


def own_archive(path: Path, shared: Path):
    shared.mkdir(parents=True, exist_ok=True)
    sha = digest(path)
    owned = shared / (sha + ".tar")
    if not owned.exists():
        shutil.copy2(path, owned)
    elif digest(owned) != sha:
        raise ValueError("Frozen shared runtime was modified")
    if not os.path.samefile(path, owned):
        path.unlink()
        os.link(owned, path)


def freeze_tree(source: Path, target: Path, shared: Path):
    def copy(src, dst):
        src, dst = Path(src), Path(dst)
        if src.name != "grader-runtime.tar":
            return shutil.copy2(src, dst)
        shared.mkdir(parents=True, exist_ok=True)
        owned = shared / (digest(src) + ".tar")
        if not owned.exists():
            shutil.copy2(src, owned)
        elif digest(owned) != digest(src):
            raise ValueError("Frozen shared runtime was modified")
        os.link(owned, dst)
        return str(dst)

    shutil.copytree(source, target, copy_function=copy, symlinks=True)
