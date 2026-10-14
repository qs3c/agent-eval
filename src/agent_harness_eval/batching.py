"""One experiment, bounded image batches, terminal-attempt resume and auditable cleanup."""

import asyncio
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from .benchmarks.swe import export, instrument, regrade_command
from .freezing import freeze_tree, own_archive
from .guardian.evidence import read, write
from .manifests import compact_manifest, load_manifest
from .provenance import digest, tree_manifest
from .report import report, task_key


def record_tasks(output, paths):
    manifest = load_manifest(output)
    cache = {}
    for task in paths:
        entry = tree_manifest(task, hash_cache=cache)
        runtime = read(task / "runtime-provenance.json", {})
        if runtime:
            entry["environment"] = {
                "base_image": runtime["image"],
                "runtime_archive_sha256": read(task / "grader-provenance.json", {}).get(
                    "archive_sha256"
                ),
            }
        manifest["tasks"][task.name] = entry
    write(output / "manifest.json", compact_manifest(output, manifest))


def prepare_batch(exp, output, paths, number):
    if not exp.batch.prepare_swe:
        return
    script = Path(__file__).resolve().parents[2] / "scripts/pin-swe-runtime.py"
    if not script.is_file():
        raise ValueError(
            "SWE preparation requires the workspace scripts/pin-swe-runtime.py"
        )
    directory = output / "batches" / f"{number:03d}"
    directory.mkdir(parents=True, exist_ok=True)
    for task in paths:
        if shutil.disk_usage(output).free < exp.batch.min_free_gib * 1024**3:
            raise RuntimeError(
                "Insufficient disk headroom during batch preparation; no trials started"
            )
        runtime = task / "environment/grader-runtime.tar"
        ready = all(
            (task / name).is_file()
            for name in [
                "runtime-provenance.json",
                "grader-provenance.json",
                "grader-files.json",
                "tests/test_spec.json",
                "environment/grader-runtime.tar",
            ]
        )
        backup = output / ".preparation-backups" / task.name
        if backup.exists():
            # No trial starts until prepare_batch completes. Restore its private transaction.
            shutil.rmtree(task)
            freeze_tree(backup, task, output / "shared-runtime")
            shutil.rmtree(backup)
            ready = all(
                (task / name).is_file()
                for name in [
                    "runtime-provenance.json",
                    "grader-provenance.json",
                    "grader-files.json",
                    "tests/test_spec.json",
                    "environment/grader-runtime.tar",
                ]
            )
        if not ready:
            freeze_tree(task, backup, output / "shared-runtime")
        try:
            if (
                "python /tests/capture_patch.py"
                not in (task / "tests/test.sh").read_text()
            ):
                instrument(task)
            with (directory / f"{task.name}.prepare.log").open("a") as log:
                for attempt in range(3):
                    result = subprocess.run(
                        [sys.executable, str(script), str(task)],
                        stdout=log,
                        stderr=subprocess.STDOUT,
                        timeout=1800,
                        check=False,
                    )
                    if not result.returncode:
                        break
                    if attempt == 2:
                        raise RuntimeError(
                            f"Preparation failed for {task.name}; inspect its batch log"
                        )
                    time.sleep(5)
            if (
                digest(runtime)
                != read(task / "grader-provenance.json")["archive_sha256"]
            ):
                raise ValueError("Prepared runtime archive hash mismatch")
            own_archive(runtime, output / "shared-runtime")
            policy = task / "task.toml"
            text = policy.read_text().replace(
                'network_mode = "no-network"',
                'network_mode = "allowlist"\nallowed_hosts = ["api.deepseek.com"]',
            )
            policy.write_text(text)
            import tomllib

            agent = tomllib.loads(text)["agent"]
            if agent["network_mode"] != "allowlist" or agent.get("allowed_hosts") != [
                "api.deepseek.com"
            ]:
                raise ValueError(
                    "Prepared SWE agent network policy is not the frozen allowlist"
                )
            record_tasks(output, [task])
            if backup.exists():
                shutil.rmtree(backup)
        except BaseException:
            if backup.exists():
                shutil.rmtree(task)
                freeze_tree(backup, task, output / "shared-runtime")
                shutil.rmtree(backup)
            raise


def regrade_batch(exp, output, paths, number):
    if not exp.batch.official_regrade:
        return
    directory = output / "batches" / f"{number:03d}" / "official"
    directory.mkdir(parents=True, exist_ok=True)
    predictions = directory / "predictions"
    # Export fresh into a temporary directory; finished trials are never rerun.
    staging = directory / "export-staging"
    if staging.exists():
        shutil.rmtree(staging)
    export(output, staging)
    predictions.mkdir(exist_ok=True)
    ids = {task.name for task in paths}
    for source in staging.glob("*.jsonl"):
        rows = [json.loads(line) for line in source.read_text().splitlines()]
        rows = [row for row in rows if row["instance_id"] in ids]
        if {row["instance_id"] for row in rows} != ids or len(rows) != len(ids):
            raise ValueError("Batch predictions do not cover the exact task set")
        (predictions / source.name).write_text(
            "".join(json.dumps(row) + "\n" for row in rows)
        )
    provenance = read(staging / "export-manifest.json")
    provenance["trials"] = [t for t in provenance["trials"] if t["instance_id"] in ids]
    provenance["batch_tasks"] = sorted(ids)
    write(predictions / "export-manifest.json", provenance)
    shutil.rmtree(staging)
    records = [read(task / "tests/config.json") for task in paths]
    dataset = directory / "records.json"
    write(dataset, records)
    errors = []
    for agent in exp.agents:
        for attempt in range(1, exp.attempts + 1):
            work = directory / agent.id / f"attempt-{attempt}"
            work.mkdir(parents=True, exist_ok=True)
            run_id = f"{exp.name}-b{number:03d}-{agent.id}-a{attempt}"
            cmd = regrade_command(
                predictions / f"{agent.id}.attempt-{attempt}.jsonl",
                str(dataset.resolve()),
                run_id,
            )
            with (work / "console.log").open("a") as log:
                result = subprocess.run(
                    cmd, cwd=work, stdout=log, stderr=subprocess.STDOUT, check=False
                )
            if result.returncode:
                errors.append(
                    {
                        "agent": agent.id,
                        "attempt": attempt,
                        "returncode": result.returncode,
                    }
                )
                continue
            reports = [read(p) for p in work.glob("*.json")]
            reports = [
                r for r in reports if isinstance(r, dict) and "submitted_instances" in r
            ]
            if len(reports) != 1 or set(reports[0].get("submitted_ids", [])) != ids:
                errors.append(
                    {
                        "agent": agent.id,
                        "attempt": attempt,
                        "error": "official_report_missing_or_wrong_task_coverage",
                    }
                )
            elif reports[0].get("error_instances", 0) or reports[0].get(
                "incomplete_ids"
            ):
                errors.append(
                    {
                        "agent": agent.id,
                        "attempt": attempt,
                        "error": "official_grading_errors_or_incomplete_instances",
                    }
                )
    write(
        directory / "status.json",
        {
            "finished_unix": time.time(),
            "errors": errors,
            "tasks": sorted(ids),
            "evaluator": "swebench==4.1.0",
        },
    )
    if errors:
        raise RuntimeError(
            "Official batch regrading failed; retain environments and resume after diagnosis"
        )
    summary = {
        "expected_per_agent": len(exp.benchmark.tasks) * exp.attempts,
        "agents": {},
    }
    for agent in exp.agents:
        rows = []
        for p in (output / "batches").glob(f"*/official/{agent.id}/attempt-*/*.json"):
            value = read(p, {})
            if "submitted_instances" in value:
                rows.append((p, value))
        observed = sum(v["submitted_instances"] for _, v in rows)
        resolved = sum(v["resolved_instances"] for _, v in rows)
        summary["agents"][agent.id] = {
            "observed": observed,
            "resolved": resolved,
            "complete": observed == summary["expected_per_agent"],
            "rate_on_observed": resolved / observed if observed else None,
            "raw_reports": [str(p.relative_to(output)) for p, _ in rows],
        }
    write(output / "official-summary.json", summary)


def cleanup_batch(exp, output, paths, number):
    refs = set()
    for task in paths:
        runtime = read(task / "runtime-provenance.json", {})
        if runtime.get("image"):
            refs.add(runtime["image"])
    wanted = {task.name for task in paths}
    for result in (output / "jobs").glob("*/*/result.json"):
        if task_key(read(result, {})) not in wanted:
            continue
        snapshot = read(result.parent / "guardian-agent-end.json", {})
        image = snapshot.get("container", {}).get("image")
        if image:
            refs.add(image)
    operations = []
    failures = []
    before = shutil.disk_usage(output).free
    for ref in sorted(refs):
        inspect = subprocess.run(
            ["docker", "image", "inspect", ref],
            capture_output=True,
            text=True,
            check=False,
        )
        if inspect.returncode:
            operations.append({"image": ref, "status": "already_absent"})
            continue
        image = json.loads(inspect.stdout)[0]
        containers = subprocess.run(
            ["docker", "ps", "-aq", "--filter", "ancestor=" + image["Id"]],
            capture_output=True,
            text=True,
            check=False,
        )
        if containers.returncode or containers.stdout.split():
            operations.append(
                {
                    "image": ref,
                    "status": "retained_container_reference",
                    "containers": containers.stdout.split(),
                }
            )
            continue
        # No --force: Docker provides the final reference check against lifecycle races.
        owned = [ref]
        if "@" in ref:
            repository = ref.split("@", 1)[0]
            owned += [
                tag
                for tag in image.get("RepoTags") or []
                if tag.rsplit(":", 1)[0] == repository
            ]
        elif ref.startswith("sha256:"):
            # The image was recorded by this trial's DockerTarget before deletion.
            managed = [
                tag for tag in image.get("RepoTags") or [] if tag.startswith("hb__")
            ]
            if not managed:
                operations.append({"image": ref, "status": "retained_unmanaged_tags"})
                continue
            owned = managed
        results = []
        for name in dict.fromkeys(owned):
            removal = subprocess.run(
                ["docker", "image", "rm", name],
                capture_output=True,
                text=True,
                check=False,
            )
            results.append(
                {
                    "reference": name,
                    "returncode": removal.returncode,
                    "error": removal.stderr[-2000:],
                }
            )
        remains = subprocess.run(
            ["docker", "image", "inspect", image["Id"]],
            capture_output=True,
            text=True,
            check=False,
        )
        remaining_tags = (
            (json.loads(remains.stdout)[0].get("RepoTags") or [])
            if remains.returncode == 0
            else []
        )
        still_owned = set(owned).intersection(remaining_tags)
        status = (
            "removal_failed"
            if still_owned
            else "retained_external_tag"
            if remaining_tags
            else "removed"
        )
        if still_owned:
            failures.append({"image": ref, "remaining_tags": sorted(still_owned)})
        operations.append({"image": ref, "status": status, "results": results})
    # Unused build cache holds otherwise deleted SWE image layers. This removes
    # cache only, never tagged images, running containers, networks or volumes.
    cache = subprocess.run(
        ["docker", "builder", "prune", "--force"],
        capture_output=True,
        text=True,
        check=False,
    )
    write(
        output / "batches" / f"{number:03d}" / "cleanup.json",
        {
            "started_free_bytes": before,
            "finished_free_bytes": shutil.disk_usage(output).free,
            "images": operations,
            "cache_returncode": cache.returncode,
            "cache_summary": cache.stdout[-500:],
            "cache_error": cache.stderr[-1000:],
            "failed_removals": failures,
        },
    )
    if cache.returncode:
        raise RuntimeError(
            "Unused build cache cleanup failed; inspect batch cleanup.json"
        )
    if failures:
        raise RuntimeError(
            "Current-batch image cleanup failed; inspect batch cleanup.json"
        )


async def run_batches(
    exp, output, tasks, agent_configs, *, resume=False, manage_signals=True
):
    from .guardian.supervisor import run_guarded

    state_path = output / "batch-state.json"
    state = read(
        state_path,
        {
            "schema_version": 1,
            "batch_size": exp.batch.size,
            "total_tasks": len(tasks),
            "batches": [],
        },
    )
    state["runner"] = {
        "pid": os.getpid(),
        "start_ticks": Path("/proc/self/stat")
        .read_text()
        .rsplit(")", 1)[1]
        .split()[19],
    }
    chunks = [
        tasks[i : i + exp.batch.size] for i in range(0, len(tasks), exp.batch.size)
    ]
    for number, chunk in enumerate(chunks, 1):
        paths = [task.path for task in chunk]
        entry = next((b for b in state["batches"] if b["number"] == number), None)
        if entry and entry["phase"] == "completed":
            continue
        if entry is None:
            entry = {
                "number": number,
                "tasks": [t.name for t in paths],
                "phase": "pending",
            }
            state["batches"].append(entry)

        def phase(value, entry=entry, number=number, **extra):
            entry.update(phase=value, updated_unix=time.time(), **extra)
            state.update(
                current_batch=number,
                total_batches=len(chunks),
                phase=value,
                updated_unix=time.time(),
            )
            write(state_path, state)

        try:
            free = shutil.disk_usage(output).free
            if free < exp.batch.min_free_gib * 1024**3:
                raise RuntimeError(
                    "Insufficient disk headroom before next batch; no trials started"
                )
            phase("preparing")
            await asyncio.to_thread(prepare_batch, exp, output, paths, number)
            record_tasks(output, paths)
            if shutil.disk_usage(output).free < exp.batch.min_free_gib * 1024**3:
                raise RuntimeError("Insufficient disk headroom before Agent execution")
            for agent in exp.agents:
                phase("running", agent=agent.id)
                policy = {
                    "max_count": exp.batch.retain_count,
                    "max_gib": exp.batch.retain_gib,
                    "ttl_hours": exp.batch.retain_hours,
                    "required_files": [
                        "agent/events.jsonl",
                        "verifier/model.patch",
                        "verifier/report.json",
                    ]
                    if exp.batch.prepare_swe
                    else ["result.json"],
                }
                await run_guarded(
                    exp,
                    output,
                    agent_configs[agent.id],
                    chunk,
                    agent.id,
                    manage_signals=manage_signals,
                    retention=policy,
                    resume=resume,
                )
                report(output)
            phase("regrading")
            try:
                await asyncio.to_thread(regrade_batch, exp, output, paths, number)
            except Exception as error:  # noqa: BLE001 - persist scoring failures without aborting Agent execution
                # Official scoring is independent of Agent execution. Retain
                # its failure evidence while allowing the next batch to run.
                entry["official_regrade"] = {
                    "status": "failed",
                    "error": str(error),
                    "updated_unix": time.time(),
                }
            else:
                entry["official_regrade"] = {
                    "status": "completed" if exp.batch.official_regrade else "disabled",
                    "updated_unix": time.time(),
                }
            entry.pop("error", None)
            phase("cleaning")
            await asyncio.to_thread(cleanup_batch, exp, output, paths, number)
            phase("completed", agent=None)
        except BaseException as error:
            phase(
                "interrupted"
                if isinstance(error, asyncio.CancelledError)
                else "failed",
                error=str(error),
            )
            raise
    state.update(phase="completed", updated_unix=time.time())
    write(state_path, state)
