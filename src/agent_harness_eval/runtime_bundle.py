"""Build a shared grader archive once per unchanged installed runtime."""

import hashlib
import json
import os
import tarfile
import tempfile
from pathlib import Path

from .manifests import atomic_json, file_identity
from .provenance import digest


def bundle_runtime(sources, cache):
    cache = Path(cache)
    cache.mkdir(parents=True, exist_ok=True)
    files = {}
    for prefix, source in sources:
        source = Path(source)
        for directory, dirs, names in os.walk(source, followlinks=True):
            current = Path(directory)
            dirs[:] = sorted(d for d in dirs if d != "__pycache__")
            ancestors = {p.resolve() for p in [current, *current.parents]}
            if any((current / d).resolve() in ancestors for d in dirs):
                raise ValueError("Runtime contains a directory symlink cycle")
            for name in sorted(names):
                path = current / name
                if name != "_uv_ephemeral_overlay.pth" and path.is_file():
                    # Materialize symlink targets, as the previous copytree did.
                    relative = path.relative_to(source)
                    files[f"{prefix}/{relative.as_posix()}"] = path.resolve()
    identities = {
        name: [str(path), file_identity(path)] for name, path in files.items()
    }
    key = hashlib.sha256(json.dumps(identities, sort_keys=True).encode()).hexdigest()
    receipt_path = cache / f"{key}.receipt.json"
    if receipt_path.exists():
        receipt = json.loads(receipt_path.read_text())
        archive = cache / receipt["archive"]
        # Linking into another task changes ctime, but not archive bytes/mtime.
        # Run freezing independently hashes the archive; this is a build cache.
        if (
            archive.exists()
            and file_identity(archive)[:4] == receipt["archive_identity"][:4]
        ):
            return {**receipt, "path": str(archive)}
    fd, tmp = tempfile.mkstemp(dir=cache, suffix=".tar")
    os.close(fd)
    hashes = {}
    try:
        with tarfile.open(tmp, "w") as tar:
            for name, path in sorted(files.items()):
                if file_identity(path) != identities[name][1]:
                    raise ValueError(f"Runtime changed while packing: {path}")
                hashes[name] = digest(path)
                info = tar.gettarinfo(str(path), arcname=name)
                info.uid = info.gid = info.mtime = 0
                info.uname = info.gname = ""
                with path.open("rb") as stream:
                    tar.addfile(info, stream)
                if file_identity(path) != identities[name][1]:
                    raise ValueError(f"Runtime changed while packing: {path}")
        sha = digest(Path(tmp))
        archive = cache / f"{sha}.tar"
        # Never replace an existing archive inode used by prepared tasks.
        if archive.exists():
            if digest(archive) != sha:
                raise ValueError("Corrupt cached runtime archive")
        else:
            os.replace(tmp, archive)
        inventory = cache / f"{sha}.files.json"
        atomic_json(inventory, hashes)
        receipt = {
            "archive": archive.name,
            "sha256": sha,
            "archive_identity": file_identity(archive),
            "inventory": inventory.name,
            "inventory_sha256": digest(inventory),
            "file_count": len(files),
        }
        atomic_json(receipt_path, receipt)
        return {**receipt, "path": str(archive)}
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def link_archive(source, target):
    """Prepared tasks share an inode; frozen runs still receive independent copies."""
    import shutil

    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(source, target)
    except OSError:
        if target.exists():
            raise
        shutil.copyfile(source, target)
