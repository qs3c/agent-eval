import hashlib
import json
import subprocess
import tarfile
from pathlib import Path


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def tree_manifest(root: Path, *, hash_cache=None) -> dict:
    files = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"task symlinks are not supported: {path}")
        if path.is_file():
            stat = path.stat()
            key = (
                stat.st_dev,
                stat.st_ino,
                stat.st_size,
                stat.st_mtime_ns,
                stat.st_ctime_ns,
            )
            if hash_cache is not None and key in hash_cache:
                sha = hash_cache[key]
            else:
                sha = digest(path)
                after = path.stat()
                if key != (
                    after.st_dev,
                    after.st_ino,
                    after.st_size,
                    after.st_mtime_ns,
                    after.st_ctime_ns,
                ):
                    raise ValueError(f"File changed during hashing: {path}")
                if hash_cache is not None:
                    hash_cache[key] = sha
            files[str(path.relative_to(root))] = sha
    return {
        "sha256": hashlib.sha256(
            json.dumps(files, sort_keys=True).encode()
        ).hexdigest(),
        "files": files,
    }


def validate_tree(root: Path):
    """Check task paths without hashing their contents just to submit a job."""
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ValueError(f"task symlinks are not supported: {path}")


def source_manifest(root: Path, evidence_dir: Path | None = None) -> dict:
    def git(*args):
        return subprocess.check_output(["git", "-C", str(root), *args])

    commit = git("rev-parse", "HEAD").decode().strip()
    patch = git("diff", "--binary", "--full-index", "HEAD", "--")
    paths = [
        p.decode()
        for p in git("ls-files", "--others", "--exclude-standard", "-z").split(b"\0")
        if p
    ]
    entries = {
        name: digest(root / name)
        for name in sorted(paths)
        if not (root / name).is_symlink()
    }
    if any((root / name).is_symlink() for name in paths):
        raise ValueError(
            "Untracked source symlinks must be committed or removed before snapshot"
        )
    status = git("status", "--porcelain")
    identity = {
        "commit": commit,
        "diff_sha256": hashlib.sha256(patch).hexdigest(),
        "untracked": entries,
    }
    result = {
        "schema_version": 2,
        "commit": commit,
        "git_tree": git("rev-parse", "HEAD^{tree}").decode().strip(),
        "dirty": bool(status),
        "source_hash_kind": "git-commit+working-diff+untracked-v2",
        "source_sha256": hashlib.sha256(
            json.dumps(identity, sort_keys=True).encode()
        ).hexdigest(),
    }
    if status:
        if evidence_dir is None:
            raise ValueError("Dirty source requires an evidence directory")
        evidence_dir.mkdir(parents=True, exist_ok=False)
        (evidence_dir / "changes.patch").write_bytes(patch)
        with tarfile.open(evidence_dir / "untracked.tar", "w") as archive:
            for name in sorted(paths):
                archive.add(root / name, arcname=name, recursive=False)
        if any(digest(root / name) != sha for name, sha in entries.items()):
            raise ValueError("Untracked source changed during snapshot")
        write_json(evidence_dir / "untracked.json", entries)
        if (
            git("status", "--porcelain") != status
            or git("diff", "--binary", "--full-index", "HEAD", "--") != patch
        ):
            raise ValueError("Source changed during snapshot")
        result["changes"] = {p.name: digest(p) for p in evidence_dir.iterdir()}
        result["changes_directory"] = evidence_dir.name
    return result


def write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    )
