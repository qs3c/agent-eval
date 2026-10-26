"""Shared deadlines, resource admission, identity and evidence for mature drivers.

The driver remains the actual multi-turn harness, not a one-shot LLM call.
Drivers must enforce tool/mount boundaries and terminate their child processes.
"""
import asyncio
import time

from ..guardian.evidence import write
from ..operations.resources import Request, async_reservation
from .protocol import PlatformAgentRequest


async def execute(request: PlatformAgentRequest, driver):
    request.output.mkdir(parents=True, exist_ok=True)
    home = request.output / ("home-" + request.role)
    home.mkdir(mode=0o700, exist_ok=True)
    frozen = request.model_dump(mode="json")
    write(request.output / "platform-request.json", frozen)
    result = {"role": request.role, "status": "running", "started_at": time.time(),
              "model_policy_digest": request.model_policy.digest if request.model_policy else None,
              "cost_usd": None, "raw_trace": str(request.output), "home": str(home)}
    try:
        async with asyncio.timeout(request.timeout_seconds), async_reservation(Request("platform-agent", request.cpus, request.memory_mb)):
            value = await driver(request, home)
        result.update(status="completed", result=value)
        return value
    except BaseException as error:
        result.update(status="interrupted" if isinstance(error, (asyncio.CancelledError, TimeoutError)) else "failed", error=type(error).__name__)
        raise
    finally:
        result["finished_at"] = time.time()
        write(request.output / "platform-result.json", result)
