"""Materialize native CLI runtimes once; never download dependencies in a trial."""
import gzip
import json
import os
import subprocess
import tarfile
from pathlib import Path

from .provenance import digest


def build_bundle(agent, output, *, binary=None, source=None, python_home=None, site_packages=None):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    if agent == "opencode":
        binary = Path(binary).resolve()
        version = subprocess.check_output([str(binary), "--version"], text=True).strip()
        roots = [("opencode", binary)]
        identity = {"agent": agent, "version": version, "binary_sha256": digest(binary),
                    "web_tools": ["websearch", "webfetch"], "search_backend": "native Exa MCP"}
    elif agent == "hermes":
        source, python_home, site_packages = map(lambda p: Path(p).resolve(), (source, python_home, site_packages))
        sha = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
        if subprocess.check_output(["git", "-C", str(source), "status", "--porcelain", "--untracked-files=no"]):
            raise ValueError("Hermes source must match its frozen commit")
        roots = [("source", source), ("python", python_home), ("site-packages", site_packages)]
        identity = {"agent": agent, "version": sha, "source_commit": sha, "lock_sha256": digest(source / "uv.lock"),
                    "python_version": subprocess.check_output([str(python_home / "bin/python3"), "--version"], text=True).strip(),
                    "web_tools": ["web_search", "web_extract"], "search_backend": "native Exa MCP (keyless)",
                    "dependency_policy": "uv sync --locked --no-dev --extra ddgs; runtime lazy installs disabled"}
    else:
        raise ValueError("Unsupported native CLI")
    inventory = {}
    archive = output / (agent + ".tar.gz")
    with archive.open("wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as compressed, tarfile.open(fileobj=compressed, mode="w|") as tar:
        for prefix, root in roots:
            entries = [(prefix, root)] if root.is_file() else []
            if root.is_dir():
                for directory, dirs, names in os.walk(root, followlinks=False):
                    dirs[:] = sorted(d for d in dirs if d not in {".git", ".venv", "__pycache__", ".pytest_cache"})
                    for name in sorted(names):
                        path = Path(directory) / name
                        if name.endswith((".pyc", ".pyo")) or name.startswith("__editable__"):
                            continue
                        entries.append((prefix + "/" + path.relative_to(root).as_posix(), path))
            for name, path in entries:
                if not path.is_file():
                    continue
                # Materialize the standalone interpreter and dependency symlinks.
                resolved = path.resolve()
                info = tar.gettarinfo(str(resolved), arcname=name)
                info.uid = info.gid = info.mtime = 0
                info.uname = info.gname = ""
                inventory[name] = digest(resolved)
                with resolved.open("rb") as stream:
                    tar.addfile(info, stream)
    (output / "files.json").write_text(json.dumps(inventory, indent=2, sort_keys=True))
    identity.update({"bundle_sha256": digest(archive), "file_inventory_sha256": digest(output / "files.json"),
                     "web_fetch_review": "platform-tool-result-review", "native_loop": True,
                     "state_isolation": "fresh trial HOME/XDG/HERMES_HOME; no cross-task state"})
    (output / "provenance.json").write_text(json.dumps(identity, indent=2))
    return identity
