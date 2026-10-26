import asyncio
import json
import os
import sys

from ..guardian.evidence import now, write
from ..guardian.process import read_bounded, terminate
from .models import environment


def expand(argv, values):
    replacements = {"python": sys.executable, **{k: str(v) for k, v in values.items()}}
    result = []
    for arg in argv:
        for key, value in replacements.items():
            arg = arg.replace("{" + key + "}", value)
        result.append(arg)
    return result


async def run(command, cwd, log=None, values=None, stdin=None, limit=1024 * 1024):
    argv = expand(command.argv, values or {})
    stamp = now()
    process = await asyncio.create_subprocess_exec(
        *argv,
        cwd=cwd,
        env=environment(command.pass_env),
        stdin=asyncio.subprocess.PIPE
        if stdin is not None
        else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=os.name == "posix",
    )
    readers = [
        asyncio.create_task(read_bounded(stream, limit))
        for stream in (process.stdout, process.stderr)
    ]
    result = {"argv": argv, "started_at": stamp}
    try:
        async with asyncio.timeout(command.timeout_seconds):
            if stdin is not None:
                process.stdin.write(
                    (json.dumps(stdin, ensure_ascii=False) + "\n").encode()
                )
                await process.stdin.drain()
                process.stdin.close()
            await process.wait()
            out, err = await asyncio.gather(*readers)
        result.update(
            returncode=process.returncode,
            stdout=out[0],
            stderr=err[0],
            truncated=out[1] or err[1],
        )
    except (TimeoutError, asyncio.CancelledError) as error:
        result.update(error=type(error).__name__, returncode=None)
        raise
    finally:
        await terminate(process)
        for task in readers:
            task.cancel()
        await asyncio.gather(*readers, return_exceptions=True)
        result["finished_at"] = now()
        if log:
            write(log, result)
    return result


async def checked(command, cwd, log=None, values=None, stdin=None, *, lane=None):
    if lane:
        from ..operations.resources import Request, async_reservation

        async with asyncio.timeout(command.timeout_seconds), async_reservation(Request(lane, 2, 4096)):
            result = await run(command, cwd, log, values, stdin)
    else:
        result = await run(command, cwd, log, values, stdin)
    if result["returncode"]:
        raise ValueError(
            f"Command exited with {result['returncode']}; inspect the local process record"
        )
    return result
