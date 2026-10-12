"""Independent per-trial supervision; a hung worker cannot own the queue forever."""

import asyncio
import signal
import sys
import time
from uuid import uuid4

from harbor.models.trial.config import TrialConfig

from .docker import DockerTarget
from .driver import run_driver
from .evidence import append, capture, now, read, write
from .monitor import Monitor
from .process import terminate


def fallback_result(root, config, reason, started):
    if (root / "result.json").exists():
        return
    result = read(root / "guardian-result-snapshot.json", {}) or {}
    result.update(
        trial_name=config.trial_name,
        task_name=config.task.path.name,
        task_id={"path": str(config.task.path)},
        finished_at=now(),
    )
    result.setdefault("started_at", started)
    result["exception_info"] = result.get("exception_info") or {
        "exception_type": "GuardianTrialTerminated",
        "exception_message": reason,
        "occurred_at": now(),
        "exception_traceback": "",
    }
    write(root / "result.json", result)


async def supervise(config, guardian, agent_id, *, backend=None, worker_argv=None):
    root = config.trials_dir / config.trial_name
    root.mkdir(parents=True, exist_ok=True)
    inputs = root.parent / ".guardian-inputs"
    write(inputs / (config.trial_name + ".json"), config.model_dump(mode="json"))
    backend = backend or DockerTarget(config.trial_name, guardian.command_seconds)
    log = (root / "guardian-worker.log").open("wb")
    try:
        process = await asyncio.create_subprocess_exec(
            *(
                worker_argv
                or [
                    sys.executable,
                    "-m",
                    "agent_harness_eval.guardian.worker",
                    str(inputs / (config.trial_name + ".json")),
                ]
            ),
            stdout=log,
            stderr=log,
            start_new_session=True,
        )
    except OSError as error:
        log.close()
        fallback_result(root, config, "worker_start_failed: " + str(error), now())
        write(
            root / "guardian-status.json",
            {"time": now(), "phase": "finished", "failure": str(error)},
        )
        return
    started, start_clock = now(), time.monotonic()
    phase, phase_since = "creating", start_clock
    monitor = Monitor(guardian)
    incident, driver, verification_until = None, None, None
    incident_pending, incident_progress = set(), False
    count, failure = 0, None
    heartbeat = root / "guardian-status.json"

    def active():
        state = read(root / "guardian-state.json", {})
        budget = (
            state.get("agent_seconds")
            or config.agent.override_timeout_sec
            or guardian.setup_seconds
        )
        return (
            process.returncode is None
            and state.get("phase") == "agent-start"
            and time.monotonic() < state.get("since", phase_since) + budget
        )

    # Recheck authority after Docker inspection, immediately before dispatch.
    if isinstance(backend, DockerTarget):
        backend.active = active

    async def finish_incident(status, extra=None):
        nonlocal incident, verification_until
        if incident:
            try:
                after = await backend.snapshot()
            except Exception as error:  # noqa: BLE001 - preserve evidence and continue cleanup on adapter failure
                after = {"error": str(error)}
            write(incident / "after.json", after)
            write(
                incident / "outcome.json",
                {"status": status, "finished_at": now(), **(extra or {})},
            )
        incident, verification_until = None, None

    try:
        while process.returncode is None:
            clock = time.monotonic()
            state = read(root / "guardian-state.json", {}) or {}
            new_phase = state.get("phase", phase)
            if new_phase != phase:
                phase, phase_since = new_phase, state.get("since", clock)
                monitor = Monitor(guardian)
                if driver:
                    driver.cancel()
                    await asyncio.gather(driver, return_exceptions=True)
                    driver = None
                await finish_incident("phase_advanced", {"phase": phase})
            budget = (
                (
                    state.get("agent_seconds")
                    or config.agent.override_timeout_sec
                    or guardian.setup_seconds
                )
                if phase == "agent-start"
                else min(
                    state.get("verifier_seconds") or guardian.verifier_seconds,
                    guardian.verifier_seconds,
                )
                if phase == "verification-start"
                else guardian.cleanup_seconds
                if phase in ("agent-end", "end", "cancel")
                else guardian.setup_seconds
            )
            write(
                heartbeat,
                {
                    "time": now(),
                    "phase": phase,
                    "elapsed_seconds": clock - phase_since,
                    "incident": incident.name if incident else None,
                    "worker_pid": process.pid,
                },
            )
            reason, changed, snapshot = None, False, {}
            if phase == "agent-start":
                try:
                    snapshot = await backend.snapshot()
                    reason, changed = monitor.observe(
                        snapshot, time.monotonic(), agent_id
                    )
                except Exception as error:  # noqa: BLE001 - preserve evidence and continue cleanup on adapter failure
                    append(root / "guardian-observer.jsonl", {"error": str(error)})
            if time.monotonic() - phase_since > budget + guardian.cleanup_seconds:
                failure = "phase_deadline_exceeded: " + phase
                break
            if incident and changed:
                incident_progress = True
            if driver and driver.done():
                try:
                    result = driver.result()
                except Exception as error:  # noqa: BLE001 - preserve evidence and continue cleanup on adapter failure
                    result = {"status": "failed", "error": str(error)}
                write(incident / "driver-result.json", result)
                driver = None
                verification_until = time.monotonic() + guardian.verification_seconds
            if driver and not active():
                driver.cancel()
                await asyncio.gather(driver, return_exceptions=True)
                driver = None
                write(incident / "driver-result.json", {"status": "authority_expired"})
            if incident and verification_until:
                # A newly started tool is useful progress; only the old stalled
                # calls must have returned. Driver stdout is not monitored here.
                if incident_progress and not incident_pending.intersection(
                    monitor.pending
                ):
                    await finish_incident("progress_observed")
                elif time.monotonic() >= verification_until:
                    failure = "recovery_not_verified"
                    break
            if reason and not incident and count < guardian.max_incidents:
                count += 1
                incident = root / "incidents" / uuid4().hex
                incident_pending, incident_progress = set(monitor.pending), False
                await capture(
                    incident,
                    backend,
                    {
                        "reason": reason,
                        "trial": config.trial_name,
                        "agent": agent_id,
                        "phase": phase,
                        "guardian": guardian.model_dump(),
                    },
                )
                if guardian.mode == "assist" and active():
                    driver = asyncio.create_task(
                        run_driver(guardian, incident, backend, active)
                    )
                else:
                    # Observation alone never kills a legitimately long silent command.
                    await finish_incident("observed_only")
            await asyncio.sleep(guardian.poll_seconds)
        if process.returncode not in (None, 0):
            failure = failure or "trial_worker_exited: " + str(process.returncode)
    except asyncio.CancelledError:
        failure = "supervisor_cancelled"
        raise
    except Exception as error:  # noqa: BLE001 - preserve evidence and continue cleanup on adapter failure
        failure = "supervisor_error: " + str(error)
    finally:
        if driver:
            driver.cancel()
            await asyncio.gather(driver, return_exceptions=True)
        if failure or process.returncode is None:
            if not incident:
                incident = root / "incidents" / uuid4().hex
                await capture(
                    incident,
                    backend,
                    {
                        "reason": failure or "worker_unfinished",
                        "trial": config.trial_name,
                        "agent": agent_id,
                        "phase": phase,
                    },
                )
            write(incident / "assisted.json", {"actor": "supervisor", "time": now()})
            append(
                incident / "actions.jsonl",
                {"kind": "fallback", "action": "stop_trial_container_and_worker"},
            )
            # Stop the producer first so it cannot recreate a container during cleanup.
            await terminate(process)
            try:
                cleanup = await asyncio.wait_for(
                    backend.stop(), guardian.cleanup_seconds
                )
            except Exception as error:  # noqa: BLE001 - preserve evidence and continue cleanup on adapter failure
                cleanup = {"error": str(error), "resources_released": False}
            append(
                incident / "actions.jsonl",
                {"kind": "fallback_result", "result": cleanup},
            )
            await finish_incident("terminated", {"reason": failure, "cleanup": cleanup})
        else:
            await finish_incident("trial_ended")
            await terminate(process)
        log.close()
        fallback_result(
            root, config, failure or "worker_exited_without_result", started
        )
        result = read(root / "result.json", {})
        if result.get("exception_info") and not list(
            (root / "incidents").glob("*/incident.json")
        ):
            # Preserve the pre-deletion boundary snapshot for fast API errors.
            event = root / "incidents" / uuid4().hex
            event.mkdir(parents=True)
            write(
                event / "incident.json",
                {
                    "schema_version": 1,
                    "reason": "trial_error",
                    "created_at": now(),
                    "trial": config.trial_name,
                    "agent": agent_id,
                },
            )
            write(
                event / "initial.json",
                read(
                    root / "guardian-agent-end.json",
                    {"error": "boundary snapshot unavailable"},
                ),
            )
            write(
                event / "outcome.json",
                {
                    "status": "failed_trial_recorded",
                    "exception": result["exception_info"],
                },
            )
        write(heartbeat, {"time": now(), "phase": "finished", "failure": failure})


async def run_guarded(exp, output, agent_config, tasks, agent_id):
    if sys.platform != "linux":
        raise ValueError("Experiment guardian execution requires Linux and Docker")
    semaphore = asyncio.Semaphore(exp.concurrency)

    async def one(task):
        async with semaphore:
            config = TrialConfig(
                task=task,
                agent=agent_config,
                trials_dir=output / "jobs" / agent_id,
                environment={
                    "type": "docker",
                    "delete": True,
                    "override_cpus": exp.cpus,
                    "override_memory_mb": exp.memory_mb,
                },
            )
            await supervise(config, exp.guardian, agent_id)

    loop, owner = asyncio.get_running_loop(), asyncio.current_task()
    previous = signal.getsignal(signal.SIGTERM)
    loop.add_signal_handler(signal.SIGTERM, owner.cancel)
    workers = [
        asyncio.create_task(one(task)) for task in tasks for _ in range(exp.attempts)
    ]
    try:
        await asyncio.gather(*workers)
    finally:
        for worker in workers:
            if not worker.done():
                worker.cancel()
        await asyncio.gather(*workers, return_exceptions=True)
        loop.remove_signal_handler(signal.SIGTERM)
        signal.signal(signal.SIGTERM, previous)
