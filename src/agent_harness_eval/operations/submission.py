"""Freeze CLI inputs before queue admission, using the same executor as Web."""

import shutil
import time
from copy import deepcopy
from pathlib import Path
from uuid import uuid4

from ..freezing import freeze_tree, plan_from_manifest, freeze_selection, selection_record
from ..provenance import digest, tree_manifest, write_json
from .commands import Commands


def submit_console(console, row):
    import json
    from ..workspace.models import identity

    commands = Commands(console.root)
    key = "console:" + row["id"]
    try:
        return commands.query(identity({"operation_key": key}))
    except KeyError:
        pass
    config = json.loads(row["config"])
    manifest = {"experiment": config, "tasks": {
        Path(name).name: tree_manifest(Path(name)) for name in config["benchmark"]["tasks"]},
        "agent_artifacts": {agent["id"]: {field: digest(Path(path))
            for field, path in agent["kwargs"].items() if field in {"binary", "bundle", "provenance"}}
            for agent in config["agents"]}}
    manifest["selection"] = selection_record(config["benchmark"]["tasks"])
    plan_id = commands.store.create_plan(plan_from_manifest(manifest), key)
    return commands.submit("console", {"run_id": row["id"], "plan_id": plan_id,
        "cooperative_pause": True}, key=key)


def submit_experiment(root, experiment, output, *, resume=False, workbench_repair=None):
    from ..console import validate_tree

    commands = Commands(root)
    key = uuid4().hex
    directory = commands.store.directory / "inputs" / key
    directory.mkdir(parents=True)
    config = experiment.model_dump(mode="json")
    requested_config = deepcopy(config)
    if resume:
        from ..workspace.models import canonical_json

        previous = [job for job in commands.list() if job["kind"] == "experiment"
                    and job["payload"].get("output") == str(Path(output).resolve())]
        if previous:
            original = previous[-1]["payload"]
            if canonical_json(original.get("requested_config")) != canonical_json(config):
                raise ValueError("Resume configuration differs from the original submission")
            config = commands.store.plan(original["plan_id"]).experiment
    try:
        if not resume:
            freeze_selection(config["benchmark"]["tasks"], directory / "tasks")
            for index, name in enumerate(config["benchmark"]["tasks"]):
                source = Path(name)
                validate_tree(source)
                target = directory / "tasks" / source.name
                freeze_tree(source, target, directory / "shared-runtime")
                config["benchmark"]["tasks"][index] = str(target)
            for agent in config["agents"]:
                for field in ("binary", "bundle", "provenance"):
                    if field not in agent["kwargs"]:
                        continue
                    source = Path(agent["kwargs"][field])
                    if source.is_symlink():
                        raise ValueError("Agent artifact must not be a symlink")
                    target = directory / "agents" / agent["id"] / source.name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, target)
                    if field == "provenance":
                        import json

                        changes = json.loads(source.read_text()).get("changes_directory")
                        if changes:
                            if Path(changes).name != changes or changes in {".", ".."}:
                                raise ValueError("Invalid provenance changes directory")
                            validate_tree(source.parent / changes)
                            shutil.copytree(source.parent / changes, target.parent / changes)
                    agent["kwargs"][field] = str(target)
        manifest = {"experiment": config,
            "tasks": {Path(name).name: tree_manifest(Path(name)) for name in config["benchmark"]["tasks"]},
            "agent_artifacts": {agent["id"]: {field: digest(Path(path))
                for field, path in agent["kwargs"].items() if field in {"binary", "bundle", "provenance"}}
                for agent in config["agents"]}}
        manifest["selection"] = selection_record(config["benchmark"]["tasks"])
        plan = plan_from_manifest(manifest)
        plan_id = commands.store.create_plan(plan, "cli:" + key)
        config_path = directory / "config.json"
        write_json(config_path, config)
        repair_path = None
        if workbench_repair:
            repair_path = directory / "repair.json"
            shutil.copy2(workbench_repair, repair_path)
        return commands.submit("experiment", {"plan_id": plan_id, "config": str(config_path),
            "requested_config": requested_config,
            "config_digest": digest(config_path), "output": str(Path(output).resolve()),
            "resume": resume, "workbench_repair": str(repair_path) if repair_path else None,
            "cooperative_pause": True}, key="cli:" + key)
    except BaseException:
        # Keep failed admission evidence for diagnosis; never delete caller inputs.
        raise


def wait(commands, job_id):
    while True:
        job = commands.query(job_id)
        if job["status"] in {"completed", "failed", "cancelled", "interrupted"}:
            return job
        time.sleep(0.25)


def verify_inputs(plan):
    from ..console import validate_tree

    if selection_record(plan.experiment["benchmark"]["tasks"]) != plan.benchmark.selection:
        raise ValueError("Frozen task selection changed")
    for name in plan.experiment["benchmark"]["tasks"]:
        path = Path(name)
        validate_tree(path)
        if tree_manifest(path)["sha256"] != plan.benchmark.task_hashes[path.name]:
            raise ValueError("Frozen task changed: " + path.name)
    for agent, release in zip(plan.experiment["agents"], plan.agents, strict=True):
        for field, expected in release.artifact_digests.items():
            if digest(Path(agent["kwargs"][field])) != expected:
                raise ValueError("Frozen agent artifact changed: " + agent["id"] + "/" + field)
