"""Trusted command adapters speak JSONL; agents make and execute decisions."""

import asyncio
import json
import os
import sys
from pathlib import Path

from .decisions import EVIDENCE_NAMES, SYSTEM_PROMPT, parse_decision
from .evidence import append, now, write
from .process import read_bounded, terminate


async def run_driver(config, incident, backend, active, *, can_act=None):
    from ..platform_agents.executor import execute
    from ..platform_agents.protocol import PlatformAgentRequest
    from ..policies.model import freeze
    from ..platform_agents.model import guardian_profile

    config = config.model_copy(deep=True)
    config.model = guardian_profile(config.model)

    request = PlatformAgentRequest(role="guardian", workspace=incident, output=incident,
        timeout_seconds=config.diagnostic_seconds, max_actions=config.max_actions,
        tools=("snapshot", "exec", "evidence", "final", "finish"),
        model_policy=freeze(config.model, "guardian") if config.model else None)
    async def driver(request, home):
        return await _run_driver(config, incident, backend, active, can_act=can_act, home=home)
    return await execute(request, driver)


async def _run_driver(config, incident, backend, active, *, can_act=None, home=None):
    can_act = can_act or active
    env = {
        k: v
        for k, v in os.environ.items()
        if k
        in {"PATH", "HOME", "LANG", "LC_ALL", "SYSTEMROOT", "PYTHONPATH"}
        | set(config.pass_env)
    }
    command = config.command
    if home:
        env["HOME"] = str(home)
    if config.model:
        name = config.model["api_key_env"]
        if not os.environ.get(name):
            raise ValueError("守护模型凭据不存在")
        env[name] = os.environ[name]
        env["PYTHONPATH"] = (
            str(Path(__file__).resolve().parents[2])
            + os.pathsep
            + env.get("PYTHONPATH", "")
        )
        command = [sys.executable, "-m", "agent_harness_eval.guardian.model_driver"]
    process = await asyncio.create_subprocess_exec(
        *command,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=incident,
        env=env,
        start_new_session=os.name == "posix",
        limit=262144,
    )
    transcript = incident / "actions.jsonl"
    stderr = asyncio.create_task(read_bounded(process.stderr, 65536))

    async def send(value):
        process.stdin.write((json.dumps(value, ensure_ascii=False) + "\n").encode())
        await process.stdin.drain()

    try:
        async with asyncio.timeout(config.diagnostic_seconds):
            context = {
                "protocol": "eval-guardian/v1",
                "incident_directory": str(incident),
                "agent_version": config.version,
                "tools": ["snapshot", "exec", "evidence", "final", "finish"],
                "max_actions": config.max_actions,
                "diagnostic_seconds": config.diagnostic_seconds,
                "model": config.model,
                "instructions": SYSTEM_PROMPT,
                "adapter_protocol": "The driver, not the LLM, emits JSONL {id,tool,arguments} on stdout.",
            }
            write(incident / "driver-context.json", context)
            await send(context)
            seen = set()
            for _ in range(config.max_actions):
                line = await process.stdout.readline()
                if not line:
                    raise RuntimeError("Guardian exited without finish")
                request = json.loads(line)
                if not isinstance(request, dict):
                    raise TypeError("Guardian request must be an object")
                call_id, tool = request.get("id"), request.get("tool")
                if not isinstance(call_id, str) or call_id in seen:
                    raise ValueError("Guardian call id must be a unique string")
                seen.add(call_id)
                args = request.get("arguments", {})
                if not isinstance(args, dict):
                    raise TypeError("Guardian arguments must be an object")
                append(
                    transcript,
                    {"kind": "request", "id": call_id, "tool": tool, "arguments": args},
                )
                if not active():
                    raise RuntimeError("Trial phase ended; guardian authority expired")
                if tool in ("finish", "final"):
                    decision = parse_decision(args).model_dump()
                    write(
                        incident / "diagnosis.json",
                        {**args, **decision, "finished_at": now()},
                    )
                    return {"status": "finished", "assessment": decision}
                try:
                    if tool == "snapshot":
                        result = await backend.snapshot()
                    elif tool == "evidence":
                        result = read_evidence(incident, args)
                    elif tool == "exec":
                        if not can_act():
                            raise RuntimeError(
                                "作答已结束或到期；仅可读取 evidence/snapshot 并提交 final，不能执行恢复命令"
                            )
                        text = args.get("command")
                        if not isinstance(text, str) or not text or len(text) > 16384:
                            raise ValueError(
                                "exec requires a command string, at most 16384 characters"
                            )
                        from ..platform_agents.roles import guardian_command

                        text = guardian_command(text)
                        # Mark before acting, including failed/unknown outcomes.
                        write(
                            incident / "assisted.json",
                            {
                                "time": now(),
                                "actor": "guardian",
                                "version": config.version,
                            },
                        )
                        result = await backend.exec(text, config.command_seconds)
                    else:
                        raise ValueError("Unknown tool: " + str(tool))
                except asyncio.CancelledError:
                    append(
                        transcript,
                        {
                            "kind": "result",
                            "id": call_id,
                            "result": {"error": "interrupted; action outcome unknown"},
                        },
                    )
                    raise
                except Exception as error:  # noqa: BLE001 - persist arbitrary adapter/tool failures
                    result = {"error": str(error)}
                append(transcript, {"kind": "result", "id": call_id, "result": result})
                await send({"id": call_id, "result": result})
            raise RuntimeError("Guardian action budget exhausted")
    finally:
        await terminate(process)
        try:
            message, truncated = await asyncio.wait_for(stderr, 2)
            write(
                incident / "driver-stderr.json",
                {"text": message, "truncated": truncated},
            )
        except (TimeoutError, asyncio.CancelledError):
            stderr.cancel()
            await asyncio.gather(stderr, return_exceptions=True)


def read_evidence(incident, arguments):
    name = arguments.get("name")
    if name not in EVIDENCE_NAMES:
        raise ValueError("Unknown evidence file")
    offset = arguments.get("offset", 0)
    if type(offset) is not int or offset < 0:
        raise ValueError("offset must be a nonnegative byte offset")
    path = (
        incident.parent.parent / name
        if name in ("guardian-agent-end.json", "guardian-state.json", "result.json")
        else incident / name
    )
    if path.is_symlink():
        raise ValueError("Evidence symlinks are not followed")
    with path.open("rb") as stream:
        size = os.fstat(stream.fileno()).st_size
        stream.seek(min(offset, size))
        data = stream.read(16384)
    return {
        "name": name,
        "offset": min(offset, size),
        "size": size,
        "next_offset": min(offset, size) + len(data),
        "text": data.decode("utf-8", "replace"),
        "truncated": min(offset, size) + len(data) < size,
    }
