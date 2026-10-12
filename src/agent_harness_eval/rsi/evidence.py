import hashlib
import re
from pathlib import Path

from ..guardian.evidence import read, write
from ..manifests import load_manifest


def create_case(
    store,
    run,
    agent,
    trials=None,
    objective="Diagnose and improve the harness using the recorded evidence",
):
    run = Path(run).resolve()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", agent):
        raise ValueError("Invalid Agent identity")
    manifest = load_manifest(run)
    agent_config = next(
        (a for a in manifest["experiment"]["agents"] if a["id"] == agent), None
    )
    if agent_config is None:
        raise ValueError("Agent is not present in this experiment")
    available = {}
    for path in (run / "jobs" / agent).glob("*/result.json"):
        if path.is_symlink() or not path.resolve().is_relative_to(run):
            continue
        result = read(path, {})
        if isinstance(result, dict) and result.get("task_name"):
            available[path.parent.name] = result
    selected = (
        list(trials)
        if trials
        else [name for name, value in available.items() if value.get("exception_info")]
    )
    if (
        not selected
        or len(selected) != len(set(selected))
        or not set(selected) <= set(available)
    ):
        raise ValueError(
            "Select recorded trials; default selection requires failed trials"
        )
    if len(objective) > 16000 or not objective.strip():
        raise ValueError("Objective must be nonempty and at most 16000 characters")
    record = store.create(
        "cases",
        {
            "run": str(run),
            "agent": agent,
            "trials": selected,
            "objective": objective,
            "status": "collecting",
        },
    )
    root = store.path("cases", record["id"]) / "evidence"
    root.mkdir()
    index, total = [], 0

    def export(path, category):
        nonlocal total
        relative = path.relative_to(run).as_posix()
        item = {
            "id": f"e{len(index) + 1:04d}",
            "source": relative,
            "category": category,
        }
        index.append(item)
        if path.is_symlink() or not path.resolve().is_relative_to(run):
            item["error"] = "symlink or path escape"
            return
        try:
            before = path.stat()
            if not path.is_file() or len(index) > 512 or total >= 32 * 1024 * 1024:
                item["error"] = "not a regular file or evidence budget exhausted"
                return
            limit = min(1024 * 1024, 32 * 1024 * 1024 - total)
            offset = max(0, before.st_size - limit)
            with path.open("rb") as f:
                f.seek(offset)
                data = f.read(limit)
            after = path.stat()
            if (before.st_size, before.st_mtime_ns) != (
                after.st_size,
                after.st_mtime_ns,
            ):
                item["error"] = "source changed during capture"
                return
            target = item["id"] + ".txt"
            (root / target).write_bytes(data)
            item.update(
                file=target,
                sha256=hashlib.sha256(data).hexdigest(),
                original_size=before.st_size,
                offset=offset,
                bytes=len(data),
                truncated=offset > 0,
                source_mtime_ns=before.st_mtime_ns,
            )
            total += len(data)
        except OSError as error:
            item["error"] = str(error)

    # Small normalized identity instead of copying huge legacy inventories.
    identity = {
        "experiment": manifest["experiment"],
        "source_agent": agent_config,
        "agent_artifacts": manifest.get("agent_artifacts", {}).get(agent, {}),
        "tasks": {
            k: {"sha256": v.get("sha256")} for k, v in manifest.get("tasks", {}).items()
        },
        "evidence_kind": manifest.get("evidence_kind"),
        "created_at": manifest.get("created_at"),
    }
    write(root / "experiment.json", identity)
    for path in sorted((run / "agent-artifacts" / agent).glob("*.json")):
        export(path, "agent_provenance")
    # Historical human intervention is supplementary evidence, not native telemetry.
    if (run / "HUMAN_INTERVENTIONS.md").is_file():
        export(run / "HUMAN_INTERVENTIONS.md", "manual_intervention_index")
    for path in sorted((run / "watchdog").glob("*.json")):
        export(path, "legacy_run_watchdog")
    for trial in selected:
        directory = run / "jobs" / agent / trial
        for name in (
            "result.json",
            "config.json",
            "trial.log",
            "exception.txt",
            "human-intervention.json",
            "guardian-state.json",
            "guardian-agent-end.json",
            "guardian-worker.log",
        ):
            export(
                directory / name,
                "manual_intervention"
                if name == "human-intervention.json"
                else "trial_artifact",
            )
        intervention = read(directory / "human-intervention.json", {})
        reference = (
            intervention.get("evidence_directory")
            if isinstance(intervention, dict)
            else None
        )
        if reference:
            referred = (run / reference).resolve()
            if referred.is_relative_to(run) and referred.is_relative_to(
                (run / "interventions").resolve()
            ):
                for path in sorted(referred.rglob("*")):
                    if len(index) >= 512:
                        break
                    if path.is_file():
                        export(path, "manual_diagnostic_snapshot")
            else:
                index.append(
                    {
                        "category": "manual_diagnostic_snapshot",
                        "error": "referenced directory outside run/interventions; not followed",
                    }
                )
        for subdir, category in (
            ("agent", "agent_trace"),
            ("verifier", "verifier_artifact"),
            ("incidents", "guardian_incident"),
        ):
            for path in sorted((directory / subdir).rglob("*")):
                if len(index) >= 512:
                    index.append(
                        {"category": category, "error": "file count limit reached"}
                    )
                    break
                if path.is_file():
                    export(path, category)
    record.update(
        status="ready",
        evidence=index,
        exported_bytes=total,
        identity_sha256=hashlib.sha256(
            (root / "experiment.json").read_bytes()
        ).hexdigest(),
    )
    write(root / "index.json", index)
    write(
        root / "case.json",
        {k: record[k] for k in ("id", "agent", "trials", "objective", "run")},
    )
    return store.save("cases", record)


def verify_case(store, case):
    root = store.path("cases", case["id"]) / "evidence"
    if read(root / "index.json") != case["evidence"]:
        raise ValueError("Frozen evidence index changed")
    expected = {k: case[k] for k in ("id", "agent", "trials", "objective", "run")}
    if read(root / "case.json") != expected:
        raise ValueError("Frozen case instructions changed")
    if (
        hashlib.sha256((root / "experiment.json").read_bytes()).hexdigest()
        != case["identity_sha256"]
    ):
        raise ValueError("Frozen experiment identity changed")
    for item in case["evidence"]:
        if "file" in item:
            path = root / item["file"]
            if (
                path.is_symlink()
                or hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]
            ):
                raise ValueError("Frozen case evidence changed")
    return root
