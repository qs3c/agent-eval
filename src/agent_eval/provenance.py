import hashlib
import json
import subprocess
from pathlib import Path


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def tree_manifest(root: Path) -> dict:
    files = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"task symlinks are not supported: {path}")
        if path.is_file():
            files[str(path.relative_to(root))] = digest(path)
    return {
        "sha256": hashlib.sha256(
            json.dumps(files, sort_keys=True).encode()
        ).hexdigest(),
        "files": files,
    }


def source_manifest(root: Path) -> dict:
    def git(*args):
        return subprocess.check_output(["git", "-C", str(root), *args])

    paths = git("ls-files", "--cached", "--others", "--exclude-standard", "-z").split(
        b"\0"
    )
    entries = {}
    for raw in sorted(set(paths)):
        if not raw:
            continue
        p = root / raw.decode()
        entries[raw.decode()] = digest(p) if p.is_file() else "deleted-or-non-file"
    return {
        "commit": git("rev-parse", "HEAD").decode().strip(),
        "dirty": bool(git("status", "--porcelain")),
        "source_sha256": hashlib.sha256(
            json.dumps(entries, sort_keys=True).encode()
        ).hexdigest(),
        "files": entries,
    }


def write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    )
