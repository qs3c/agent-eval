"""Reproducible evaluation copies; repository code never executes on the host."""

import io
import json
import os
import posixpath
import shutil
import subprocess
import tarfile
import time
from pathlib import Path
from uuid import uuid4

from ..provenance import digest, write_json
from ..workspace.models import AgentRelease, SourceRevision, identity
from .schema import Recipe


def git(source, *args):
    environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    return subprocess.check_output(["git", "-C", str(source), *args], timeout=60, env=environment)


def unpack(archive, destination, *, internal_links=False):
    destination = Path(destination)
    with tarfile.open(fileobj=io.BytesIO(archive)) as stream:
        members = stream.getmembers()
        names = [item.name.rstrip("/") for item in members]
        if len(names) != len(set(names)):
            raise ValueError("Duplicate archive member")
        links = {}
        for item in members:
            path = Path(item.name)
            if (path.is_absolute() or ".." in path.parts or "\\" in item.name
                    or ":" in item.name or not (item.isfile() or item.isdir() or internal_links and item.issym())):
                raise ValueError("Source/archive contains an unsupported path or link")
            if item.issym():
                target = posixpath.normpath(posixpath.join(posixpath.dirname(item.name), item.linkname))
                if item.linkname.startswith("/") or "\\" in item.linkname or ":" in item.linkname or target == ".." or target.startswith("../"):
                    raise ValueError("Source symlink escapes archive")
                links[item.name] = target
        if any(name.startswith(link + "/") for link in links for name in names):
            raise ValueError("Archive member traverses a symlink")
        stream.extractall(destination, members=[item for item in members if not item.issym()], filter="data")
        def materialize(name, visiting=()):
            if len(visiting) > 32 or name in visiting:
                raise ValueError("Cyclic source symlink")
            path = destination / name
            if path.exists():
                return
            target = links[name]
            if target in links:
                materialize(target, (*visiting, name))
            for child in links:
                if child.startswith(target.rstrip("/") + "/"):
                    materialize(child, (*visiting, name))
            source = destination / target
            if not source.exists() or source == path or path.is_relative_to(source):
                raise ValueError("Unsupported directory symlink or missing target")
            path.parent.mkdir(parents=True, exist_ok=True)
            if source.is_dir():
                shutil.copytree(source, path)
            else:
                shutil.copy2(source, path)
        for name in links:
            materialize(name)


def pack(source, target):
    with tarfile.open(target, "w") as stream:
        for path in sorted(Path(source).rglob("*")):
            if path.is_symlink() or not (path.is_file() or path.is_dir()):
                raise ValueError("Build artifacts must be regular files/directories")
            info = stream.gettarinfo(str(path), path.relative_to(source).as_posix())
            info.uid = info.gid = info.mtime = 0
            info.uname = info.gname = ""
            if path.is_file():
                with path.open("rb") as data:
                    stream.addfile(info, data)
            else:
                stream.addfile(info)


def resolve_image(spec):
    image = spec.image
    if spec.image_id:
        inspected = subprocess.run(["docker", "image", "inspect", "--format", "{{.Id}}", image], capture_output=True, text=True, timeout=20)
        if inspected.returncode:
            image = spec.image_id  # Offline imports may lack the original RepoDigests.
            inspected = subprocess.run(["docker", "image", "inspect", "--format", "{{.Id}}", image], capture_output=True, text=True, timeout=20)
        if inspected.returncode or inspected.stdout.strip() != spec.image_id:
            raise ValueError("Build image digest mismatch or missing pinned image")
    return image


def docker_build(recipe, directory, log):
    from ..operations.resources import Request, Resources, cancellation_requested
    from ..operations.recovery import start_ticks

    spec = recipe.build
    image = resolve_image(spec)
    actual_id = subprocess.check_output(["docker", "image", "inspect", "--format", "{{.Id}}", image], text=True, timeout=20).strip()
    runtime = {"requested": spec.image, "executed": image, "image_id": actual_id}
    write_json(Path(log).parent / "build-runtime.json", runtime)
    name = "harness-build-" + uuid4().hex
    argv = ["docker", "run", "--name", name, "--pull", "never", "--network", spec.network,
            "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--pids-limit", "256", "--cpus", str(spec.cpus), "--memory", str(spec.memory_mb) + "m",
            "--user", f"{os.getuid()}:{os.getgid()}", "--tmpfs", "/tmp:rw,nosuid,nodev,size=512m",
            "--mount", f"type=bind,src={directory.resolve()},dst=/source",
            "--workdir", "/source", "--env", "HOME=/tmp/home", "--entrypoint", spec.argv[0],
            image, *spec.argv[1:]]
    ledger = Resources()
    owner = os.environ.get("HARNESS_OPERATION_ID") or f"build:{os.getpid()}:{start_ticks(os.getpid())}"
    generation = int(os.environ.get("HARNESS_OPERATION_GENERATION", "1"))
    deadline = time.monotonic() + spec.timeout_seconds
    while True:
        if cancellation_requested() or time.monotonic() >= deadline:
            raise InterruptedError("Build admission cancelled or timed out")
        decision = ledger.acquire(Request("build", spec.cpus, spec.memory_mb), key=name, owner=owner, generation=generation)
        if decision["status"] == "granted":
            break
        if decision["status"] != "wait":
            raise ValueError("Build exceeds host budget: " + str(decision))
        time.sleep(.1)
    try:
        try:
            with Path(log).open("wb") as output:
                subprocess.run(argv, stdout=output, stderr=subprocess.STDOUT, timeout=spec.timeout_seconds, check=True)
        finally:
            # Exact unique name; never prune images/cache or another container.
            removed = subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=30)
            if removed.returncode != 0 and b"No such container" not in removed.stderr:
                raise RuntimeError("Build container cleanup unconfirmed; capacity retained")
            ledger.release(name, owner=owner, generation=generation)
    except BaseException:
        # An unconfirmed cleanup intentionally leaves capacity for reconciliation.
        raise
    return runtime


def build(recipe: Recipe, source, output, *, recipe_directory, executor=docker_build):
    source, output, recipe_directory = Path(source).resolve(), Path(output).resolve(), Path(recipe_directory).resolve()
    if Path(git(source, "rev-parse", "--show-toplevel").decode().strip()).resolve() != source:
        raise ValueError("Source must be the repository root")
    if output.is_relative_to(source):
        raise ValueError("Build output must be outside the upstream checkout")
    if git(source, "status", "--porcelain", "--untracked-files=all").strip():
        raise ValueError("Dirty upstream cannot be recorded as a clean revision")
    if recipe.source.repository != str(source):
        try:
            origin = git(source, "remote", "get-url", "origin").decode().strip()
        except subprocess.CalledProcessError as exc:
            raise ValueError("Source repository identity requires a matching origin or explicit local path") from exc
        if origin != recipe.source.repository:
            raise ValueError("Recipe repository differs from checkout origin")
    commit = git(source, "rev-parse", "--verify", "HEAD^{commit}").decode().strip()
    if recipe.source.ref and not recipe.source.commit:
        selected = git(source, "rev-parse", "--verify", recipe.source.ref + "^{commit}").decode().strip()
        if selected != commit:
            raise ValueError("Checkout differs from selected ref")
    if recipe.source.commit and recipe.source.commit != commit:
        raise ValueError("Checkout differs from pinned recipe source")
    if any(line.startswith(b"160000 ") for line in git(source, "ls-tree", "-r", "HEAD").splitlines()):
        raise ValueError("Submodules require an explicit materialized dependency recipe")
    archive = git(source, "archive", "--format=tar", commit)
    patches = []
    for name in recipe.patches:
        path = recipe_directory / name
        if path.is_symlink() or not path.resolve().is_relative_to(recipe_directory):
            raise ValueError("Patch escapes recipe directory")
        patches.append((name, path.read_bytes()))
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "recipe.json", recipe.model_dump(mode="json"))
    (output / "source.tar").write_bytes(archive)
    evaluation = output / "eval"
    evaluation.mkdir()
    try:
        unpack(archive, evaluation, internal_links=recipe.materialize_internal_links)
        patch_environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        patch_environment.update(GIT_CEILING_DIRECTORIES=str(output), GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
        for index, (name, data) in enumerate(patches):
            path = output / f"patch-{index}.diff"
            path.write_bytes(data)
            subprocess.run(["git", "apply", "--check", str(path)], cwd=evaluation, check=True, capture_output=True, timeout=60, env=patch_environment)
            subprocess.run(["git", "apply", str(path)], cwd=evaluation, check=True, capture_output=True, timeout=60, env=patch_environment)
        runtime = executor(recipe, evaluation, output / "build.log")
        artifacts = evaluation / recipe.build.output
        if not artifacts.is_dir() or artifacts.is_symlink() or not artifacts.resolve().is_relative_to(evaluation):
            raise ValueError("Builder did not produce the declared artifact directory")
        bundle = output / "agent.tar"
        pack(artifacts, bundle)
        release = AgentRelease(agent_id=recipe.id,
            source=SourceRevision(repository=recipe.source.repository, ref=recipe.source.ref, commit=commit),
            recipe_digest=recipe.digest, patch_digest=identity([{ "path": name, "sha256": digest(output / f"patch-{i}.diff")}
                for i, (name, _) in enumerate(patches)]), artifact_digests={"bundle": digest(bundle)},
            runtime_digest=runtime["image_id"] if runtime else recipe.build.image, profile=recipe.profile, provenance="recorded")
        receipt = {"release_id": identity(release), "release": release.model_dump(mode="json"),
                   "recipe": recipe.model_dump(mode="json"), "bundle_sha256": digest(bundle), "source_archive_sha256": digest(output / "source.tar"),
                   "build_runtime": runtime}
        write_json(output / "provenance.json", receipt)
        write_json(output / "status.json", {"state": "built", "release_id": receipt["release_id"]})
        return receipt
    except BaseException as exc:
        write_json(output / "status.json", {"state": "failed", "error": type(exc).__name__, "message": str(exc)})
        raise
