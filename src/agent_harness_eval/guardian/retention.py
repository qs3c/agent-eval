"""Bounded diagnostic workspaces; original containers and credentials are removed."""

import asyncio
import json
import re
import shutil
import subprocess
import tarfile
import threading
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import PurePosixPath

from .evidence import incident_summary, now, read, write
from .process import command

_LOCK = threading.Lock()
_SECRET = re.compile(
    r"(^|[._-])(key|token|secret|credential|password)([._-]|$)", re.IGNORECASE
)


@contextmanager
def archive_guard(output, *, blocking=True):
    import fcntl

    path = output / ".retention.lock"
    if path.is_symlink():
        raise ValueError("Retention lock cannot be a symlink")
    with path.open("a") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        yield


def sweep(output, policy):
    """Expire only our completed snapshots, even when no new archive is produced."""
    with _LOCK, archive_guard(output, blocking=False):
        entries = []
        for record in (output / "jobs").glob("*/*/retention.json"):
            archive = record.parent / "retained-workspace.tar"
            if (any(p.is_symlink() for p in (record, archive, record.parent, record.parent.parent))
                    or not archive.is_file() or not archive.resolve().is_relative_to(output.resolve())):
                continue
            value = read(record, {})
            if value.get("status") != "snapshot_retained":
                continue
            try:
                created = datetime.fromisoformat(value["created_at"])
                if created.tzinfo is None:
                    continue
            except (KeyError, TypeError, ValueError):
                continue
            entries.append((created, record, archive, value))
        entries.sort(key=lambda row: row[0])
        total = sum(row[2].stat().st_size for row in entries)
        cutoff = datetime.now(UTC) - timedelta(hours=policy["retain_hours"])
        removed = []
        while entries and (entries[0][0] < cutoff or len(entries) > policy["retain_count"]
                           or total > policy["retain_gib"] * 1024**3):
            _, record, archive, value = entries.pop(0)
            total -= archive.stat().st_size
            archive.unlink()
            value.update(status="snapshot_evicted", evicted_at=now(), eviction_reason="periodic_quota_or_ttl")
            write(record, value)
            removed.append(str(archive))
        return removed


def reasons(root, policy):
    result = read(root / "result.json", {})
    found = []
    if not isinstance(result, dict) or not result.get("finished_at"):
        found.append("incomplete_result")
    if isinstance(result, dict) and result.get("exception_info"):
        found.append("trial_exception")
    if incident_summary(root)["assisted"]:
        found.append("externally_assisted")
    for name in policy.get(
        "required_files",
        ["agent/events.jsonl", "verifier/model.patch", "verifier/report.json"],
    ):
        path = root / name
        if (
            not path.resolve().is_relative_to(root.resolve())
            or path.is_symlink()
            or not path.is_file()
        ):
            found.append("missing_evidence:" + name)
    return found


async def project_containers(backend):
    result = await command(
        [
            "docker",
            "ps",
            "-aq",
            "--no-trunc",
            "--filter",
            "label=com.docker.compose.project=" + backend.project,
        ],
        backend.timeout,
    )
    if result["returncode"]:
        raise RuntimeError("Cannot list trial containers: " + result["stderr"][:300])
    records = []
    for cid in result["stdout"].split():
        if not re.fullmatch(r"[0-9a-f]{64}", cid):
            raise RuntimeError("Invalid Docker container identity")
        detail = await command(
            [
                "docker",
                "inspect",
                "--format",
                "{{json .Id}}\n{{json .Config.Labels}}\n{{json .State}}",
                cid,
            ],
            backend.timeout,
        )
        if detail["returncode"]:
            raise RuntimeError("Cannot inspect trial container")
        identity, labels, state = map(json.loads, detail["stdout"].splitlines())
        if (
            identity != cid
            or labels.get("com.docker.compose.project") != backend.project
        ):
            raise RuntimeError("Trial project identity mismatch")
        records.append({"id": cid, "labels": labels, "state": state})
    return records


async def stop_project(backend):
    """Stop precisely identified containers without deleting the forensic workspace."""
    records = await project_containers(backend)
    for record in records:
        if record["state"]["Running"]:
            stopped = await command(
                ["docker", "stop", "--time", "1", record["id"]], backend.timeout
            )
            if stopped["returncode"]:
                # Last resort must release active producers even if graceful stop failed.
                await command(["docker", "kill", record["id"]], backend.timeout)
    records = await project_containers(backend)
    return {
        "stopped": all(not r["state"]["Running"] for r in records),
        "containers": records,
    }


def _secret(member):
    path = PurePosixPath(member.name)
    return (
        path.is_absolute()
        or ".." in path.parts
        or not (member.isdir() or member.isfile())
        or any(
            p == ".env"
            or p.startswith(".env.")
            or p in {".ssh", ".aws", ".docker", "installed-agent"}
            for p in path.parts
        )
        or bool(_SECRET.search(path.name))
        or path.suffix.lower() in {".key", ".pem"}
    )


def _archive(root, cid, policy):
    """Copy only /testbed; no container Env, credentials directory or image layers."""
    with _LOCK, archive_guard(root.parents[2]):
        base = root.parents[2] / "jobs"
        count = int(policy.get("max_count", 3))
        limit = int(float(policy.get("max_gib", 20)) * 1024**3)
        ttl = timedelta(hours=float(policy.get("ttl_hours", 24)))
        if count < 1 or limit < 10240 or ttl.total_seconds() <= 0:
            raise ValueError("Invalid bounded retention policy")
        retained = []
        for record in sorted(base.glob("*/*/retention.json")):
            if record.is_symlink() or record.parent.is_symlink():
                continue
            value = read(record, {})
            archive = record.parent / "retained-workspace.tar"
            if (
                value.get("status") != "snapshot_retained"
                or not archive.is_file()
                or archive.is_symlink()
            ):
                continue
            created = datetime.fromisoformat(value["created_at"])
            retained.append((created, record, archive, value))
        retained.sort(key=lambda row: row[0])
        cutoff = datetime.now(UTC) - ttl
        total = sum(row[2].stat().st_size for row in retained)
        while retained and (
            retained[0][0] < cutoff or len(retained) >= count or total >= limit
        ):
            _, record, archive, value = retained.pop(0)
            total -= archive.stat().st_size
            archive.unlink()
            value.update(
                status="snapshot_evicted",
                evicted_at=now(),
                eviction_reason="retention_quota_or_ttl",
            )
            write(record, value)
        available = min(
            limit - total, max(0, shutil.disk_usage(root).free - 2 * 1024**3)
        )
        temporary = root / "retained-workspace.tar.part"
        archive = root / "retained-workspace.tar"
        # No API keys appear in arguments, inspect output or retained archive metadata.
        process = subprocess.Popen(
            ["docker", "cp", cid + ":/testbed/.", "-"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        timer = threading.Timer(
            float(policy.get("snapshot_seconds", 120)), process.kill
        )
        timer.start()
        skipped = 0
        try:
            with (
                tarfile.open(fileobj=process.stdout, mode="r|") as source,
                tarfile.open(temporary, "w") as target,
            ):
                for member in source:
                    if _secret(member):
                        skipped += 1
                        continue
                    # Includes TAR headers, padding and final blocks; never allocate an oversized snapshot.
                    needed = ((member.size + 511) // 512 + 1) * 512 + 20480
                    if target.fileobj.tell() + needed > available:
                        raise RuntimeError(
                            "Workspace exceeds remaining retention quota"
                        )
                    target.addfile(
                        member, source.extractfile(member) if member.isfile() else None
                    )
            if process.wait(timeout=5):
                raise RuntimeError(
                    "Workspace snapshot unavailable (/testbed missing or Docker copy failed)"
                )
            if temporary.stat().st_size > available:
                raise RuntimeError("Workspace archive exceeds retention quota")
            temporary.replace(archive)
            snapshot = {
                "status": "snapshot_retained",
                "archive": archive.name,
                "bytes": archive.stat().st_size,
                "skipped_entries": skipped,
                "scope": "/testbed only; excludes credentials, links and container Env",
                "created_at": now(),
            }
            # Publish while holding the lock, so the next concurrent trial counts it.
            write(
                root / "retention.json",
                {**read(root / "retention.json", {}), **snapshot},
            )
            return snapshot
        finally:
            timer.cancel()
            if process.poll() is None:
                process.kill()
            process.wait()
            process.stdout.close()
            temporary.unlink(missing_ok=True)


def _publish(root, record):
    with _LOCK, archive_guard(root.parents[2]):
        current = read(root / "retention.json", {})
        if (
            record["status"] == "snapshot_retained"
            and not (root / "retained-workspace.tar").is_file()
        ):
            if current.get("status") == "snapshot_evicted":
                record.update(
                    {
                        key: current[key]
                        for key in ("status", "evicted_at", "eviction_reason")
                    }
                )
            else:
                record.update(
                    status="retention_failed",
                    error="Snapshot vanished before retention completed",
                )
        write(root / "retention.json", record)
    return record


async def finalize(root, backend, policy):
    """Record actual archival and cleanup outcomes; never claim preservation on failure."""
    record = {
        "schema_version": 1,
        "created_at": now(),
        "project": backend.project,
        "reasons": reasons(root, policy),
        "status": "pending",
    }
    write(root / "retention.json", record)
    try:
        stopped = await stop_project(backend)
        record["stopped"] = stopped["stopped"]
        if not stopped["stopped"]:
            raise RuntimeError("Trial containers are still active")
        main = [
            r
            for r in stopped["containers"]
            if r["labels"].get("com.docker.compose.service") == "main"
        ]
        if record["reasons"] and int(policy.get("max_count", 3)) == 0:
            record["status"] = "not_retained_policy_disabled"
        elif record["reasons"]:
            if len(main) != 1:
                raise RuntimeError("Expected one stopped main container for snapshot")
            record.update(
                await asyncio.to_thread(_archive, root, main[0]["id"], policy)
            )
        else:
            record["status"] = "not_retained_normal_completion"
    except Exception as error:  # noqa: BLE001 - failed retention must remain visible
        record.update(status="retention_failed", error=str(error))
    finally:
        try:
            cleanup = await backend.stop()
            record["cleanup"] = cleanup
            if not cleanup.get("resources_released", False):
                record["cleanup_failed"] = True
        except Exception as error:  # noqa: BLE001 - preserve cleanup failures
            record["cleanup_failed"] = True
            record["cleanup_error"] = str(error)
        await asyncio.to_thread(_publish, root, record)
    return record
