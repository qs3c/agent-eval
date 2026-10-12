"""Bounded subprocess output and process-group teardown (Linux execution)."""

import asyncio
import os
import signal


async def terminate(process):
    # Also reap descendants when the process leader has already exited.
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    elif process.returncode is None:
        process.kill()
    try:
        await asyncio.wait_for(process.wait(), 5)
    except TimeoutError:
        pass


async def read_bounded(stream, limit):
    data = bytearray()
    total = 0
    while chunk := await stream.read(8192):
        total += len(chunk)
        data.extend(chunk[: max(0, limit - len(data))])
    return data.decode("utf-8", "replace"), total > limit


async def command(argv, timeout=20, limit=1024 * 1024):
    process = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=os.name == "posix",
    )
    reads = [
        asyncio.create_task(read_bounded(s, limit))
        for s in (process.stdout, process.stderr)
    ]
    try:
        async with asyncio.timeout(timeout):
            await process.wait()
            output = await asyncio.gather(*reads)
        return {
            "returncode": process.returncode,
            "stdout": output[0][0],
            "stderr": output[1][0],
            "truncated": any(x[1] for x in output),
        }
    finally:
        await terminate(process)
        for read in reads:
            read.cancel()
        await asyncio.gather(*reads, return_exceptions=True)
