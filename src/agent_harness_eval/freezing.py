"""Independent frozen trees with one owned copy of each shared grader archive."""

import os
import shutil
from pathlib import Path

from .provenance import digest


def own_archive(path: Path, shared: Path):
    shared.mkdir(parents=True, exist_ok=True)
    sha = digest(path)
    owned = shared / (sha + ".tar")
    if not owned.exists():
        shutil.copy2(path, owned)
    elif digest(owned) != sha:
        raise ValueError("Frozen shared runtime was modified")
    if not os.path.samefile(path, owned):
        path.unlink()
        os.link(owned, path)


def freeze_tree(source: Path, target: Path, shared: Path):
    def copy(src, dst):
        src, dst = Path(src), Path(dst)
        if src.name != "grader-runtime.tar":
            return shutil.copy2(src, dst)
        shared.mkdir(parents=True, exist_ok=True)
        owned = shared / (digest(src) + ".tar")
        if not owned.exists():
            shutil.copy2(src, owned)
        elif digest(owned) != digest(src):
            raise ValueError("Frozen shared runtime was modified")
        os.link(owned, dst)
        return str(dst)

    shutil.copytree(source, target, copy_function=copy, symlinks=True)
