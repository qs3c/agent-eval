"""Fixed-SHA builtin source acquisition; no repository hooks run on the host."""

import os
import shutil
import subprocess
from pathlib import Path


def fetch_source(source, directory, log):
    from .sources import repository

    if not source.commit:
        raise ValueError("Source fetch requires a pinned commit")
    repository(source.repository)
    directory = Path(directory)
    directory.parent.mkdir(parents=True, exist_ok=True)
    if directory.exists():
        raise ValueError("Source acquisition directory already exists")
    if shutil.disk_usage(directory.parent).free < 2 * 1024**3:
        raise ValueError("Insufficient disk space for source acquisition")
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_TERMINAL_PROMPT="0")
    with Path(log).open("wb") as output:
        for argv in (["init", "-q", str(directory)], ["-C", str(directory), "remote", "add", "origin", source.repository],
                     ["-C", str(directory), "-c", "http.version=HTTP/1.1", "fetch", "--depth=1", "origin", source.commit],
                     ["-C", str(directory), "-c", "core.hooksPath=/dev/null", "checkout", "--detach", source.commit]):
            subprocess.run(["git", *argv], env=env, stdout=output, stderr=subprocess.STDOUT, check=True, timeout=300)
