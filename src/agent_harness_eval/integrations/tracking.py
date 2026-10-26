"""Bounded source refresh workers; observations never rewrite an active Plan."""
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

from .sources import Sources, repository
from ..operations.commands import Commands, TERMINAL
from ..workspace.models import identity
from ..provenance import digest, write_json


def probe(source):
    repo, ref = source["repository"], source["ref"]
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_TERMINAL_PROMPT="0")
    if Path(repo).is_absolute():
        argv = ["git", "-C", repo, "rev-parse", "--verify", ref + "^{commit}"]
    else:
        selected = ref if ref == "HEAD" or ref.startswith("refs/") else "refs/heads/" + ref
        argv = ["git", "-c", "http.version=HTTP/1.1", "ls-remote", "--exit-code", "--", repo, selected]
    result = subprocess.run(argv, env=env, capture_output=True, text=True, timeout=20)
    if result.returncode or not result.stdout.strip():
        raise ValueError("来源 ref 不存在或 Git 服务暂时不可用")
    rows = result.stdout.splitlines()
    if len(rows) != 1:
        raise ValueError("来源 ref 不唯一")
    return rows[0].split()[0]


def queue_refresh(root, source_id, *, observe=True):
    sources, commands = Sources(root), Commands(root)
    with sources.transaction() as db:
        row = db.execute("SELECT * FROM sources WHERE id=?", (source_id,)).fetchone()
        if row is None:
            raise KeyError(source_id)
        if row["refresh_job"]:
            previous = commands.query(row["refresh_job"])
            if previous["status"] not in TERMINAL:
                return previous
        sequence = row["poll_sequence"] + 1
        job = commands.submit("source-refresh", {"source_id": source_id, "observe": observe}, key=f"source-refresh:{source_id}:{sequence}")
        db.execute("UPDATE sources SET refresh_job=?,poll_sequence=?,next_poll=? WHERE id=?",
                   (job["id"], sequence, time.time() + 60 if observe else row["next_poll"], source_id))
        return job


def tick(root):
    if not (Path(root) / ".workspace/sources.sqlite").exists():
        return
    sources, commands = Sources(root), Commands(root)
    for source in sources.list():
        if not source["enabled"]:
            continue
        due = source["next_poll"] <= time.time()
        completed = any(row["status"] in {"building", "checking"} and (job := row["run_job"] or row["job"])
                        and commands.query(job)["status"] in TERMINAL for row in sources.revisions(source["id"]))
        if due or completed:
            queue_refresh(root, source["id"], observe=due)


def recipe_for(source):
    from .catalog import RECIPES
    from .schema import load

    if source["recipe"]:
        return Path(source["recipe"])
    for path in RECIPES.glob("*.yaml"):
        if repository(load(path).source.repository) == source["repository"]:
            return path
    return None


def snapshot(root, source, recipe_path):
    from .schema import load

    original = load(recipe_path)
    if original.build.artifact_origin != "source":
        raise ValueError("该配方使用发布包，不能把源码更新当成新产物；请提供 source 构建配方")
    recipe = original.model_copy(update={"source": original.source.model_copy(update={"repository": source["repository"], "ref": source["ref"], "commit": source["observed"]})})
    if repository(original.source.repository) != source["repository"]:
        raise ValueError("修复配方的仓库与来源不一致")
    hashes = {}
    for name in original.patches:
        path = recipe_path.parent / name
        if path.is_symlink() or not path.resolve().is_relative_to(recipe_path.parent.resolve()):
            raise ValueError("Patch escapes recipe directory")
        hashes[name] = digest(path)
    bundle_digest = identity({"recipe": recipe.digest, "patches": hashes})
    directory = Path(root) / ".workspace/source-inputs" / source["id"] / bundle_digest
    directory.mkdir(parents=True, exist_ok=True)
    for name in original.patches:
        path = recipe_path.parent / name
        if path.is_symlink() or not path.resolve().is_relative_to(recipe_path.parent.resolve()):
            raise ValueError("Patch escapes recipe directory")
        target = directory / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and digest(target) != digest(path):
            raise ValueError("同一配方身份的 patch 已变化；请创建独立 repair recipe")
        if not target.exists():
            shutil.copy2(path, target)
    path = directory / "recipe.json"
    if path.exists() and load(path).digest != recipe.digest:
        raise ValueError("Frozen source recipe changed")
    write_json(path, recipe.model_dump(mode="json"))
    key = identity({"source": source["id"], "sha": source["observed"], "recipe": recipe.digest,
                    "patches": {name: digest(directory / name) for name in recipe.patches}})
    return path, key


def ensure_build(root, source):
    from .commands import submit

    sources = Sources(root)
    revision = next((row for row in sources.revisions(source["id"]) if row["sha"] == source["observed"]), None)
    if not revision or revision["status"] not in {"observed", "needs_recipe"}:
        return
    recipe_path = recipe_for(source)
    if recipe_path is None:
        sources.update_revision(source["id"], source["observed"], status="needs_recipe", error="未找到标准配方；可运行 Integration Agent 或提供 recipe")
        return
    try:
        frozen, key = snapshot(root, source, recipe_path)
        key = identity({"admission": key, "attempt": revision["attempt"]})
        job = submit(root, frozen, key=key, allow_fetch=True)
        sources.update_revision(source["id"], source["observed"], status="building", job=job["id"], error=None)
    except (ValueError, OSError) as error:
        sources.update_revision(source["id"], source["observed"], status="integration_failed", error=str(error))


def advance(root, source):
    sources = Sources(root)
    for row in sources.revisions(source["id"]):
        if row["status"] not in {"building", "checking"}:
            continue
        try:
            _advance(root, source, row)
        except (ValueError, OSError, KeyError) as error:
            sources.update_revision(source["id"], row["sha"], status="integration_failed", error=str(error))


def _advance(root, source, revision):
    from .store import Releases, verify
    from .conformance import check
    from ..config import Agent, Model, load
    from ..operations.submission import submit_experiment

    sources, commands, catalog = Sources(root), Commands(root), Releases(root)
    for row in [revision]:
        if row["status"] == "building":
            job = commands.query(row["job"])
            if job["status"] not in TERMINAL:
                if not row["pinned"] and row["sha"] != source["observed"] and job["status"] in {"queued", "held"}:
                    commands.control(job["id"], "cancel")
                    sources.update_revision(source["id"], row["sha"], status="superseded", error="后续更新取代了尚未开始的构建")
                continue
            if job["status"] != "completed":
                sources.update_revision(source["id"], row["sha"], status="integration_failed", error=job["error"] or job["status"])
                continue
            receipt = verify(Path(job["payload"]["output"]))
            release_id = receipt["release_id"]
            output = Path(root) / "artifacts" / ("admission-" + release_id)
            existing = next((j for j in commands.list() if j["kind"] == "experiment" and j["payload"].get("output") == str(output.resolve())), None)
            if existing is None:
                exp = load(Path(root) / "experiments/controls.json", validate_agent_artifacts=False)
                modes = receipt["recipe"]["model_modes"]
                mode = "mock" if "mock" in modes else "offline" if "offline" in modes else None
                if not mode:
                    sources.update_revision(source["id"], row["sha"], status="integration_failed", error="自动准入需要 mock/offline 控制模式；不会隐式调用付费模型")
                    continue
                exp.agents = [Agent.model_validate(catalog.agent(release_id))]
                exp.model = Model(mode=mode)
                exp.attempts, exp.concurrency, exp.guardian = 1, 1, None
                existing = submit_experiment(root, exp, output)
            sources.update_revision(source["id"], row["sha"], status="checking", release=release_id, run_job=existing["id"])
        elif row["status"] == "checking":
            job = commands.query(row["run_job"])
            if job["status"] not in TERMINAL:
                continue
            try:
                value = check(catalog.directory / row["release"], Path(job["payload"]["output"]))
                sources.admit(source["id"], row["sha"], row["release"], value)
            except (ValueError, OSError) as error:
                sources.update_revision(source["id"], row["sha"], status="integration_failed", error=str(error))


def refresh(root, source_id, *, observe=True, resolver=probe):
    from ..operations.manager import exclusive

    sources = Sources(root)
    sources.get(source_id)
    with exclusive(sources.path.parent / ("source-" + source_id + ".lock")):
        source = sources.get(source_id)
        advance(root, source)
        if observe:
            try:
                sources.observe(source_id, resolver(source))
            except (ValueError, OSError, subprocess.TimeoutExpired) as error:
                sources.failure(source_id, str(error))
                return sources.get(source_id)
        source = sources.get(source_id)
        advance(root, source)
        ensure_build(root, source)
        for row in sources.revisions(source_id):
            if row["pinned"] and row["status"] == "observed":
                ensure_build(root, {**source, "observed": row["sha"]})
        return sources.get(source_id)


def perform(root, job_id):
    from ..operations.worker import is_managed_worker

    if not is_managed_worker():
        raise ValueError("Source refresh requires Manager ownership")
    payload = Commands(root).query(job_id)["payload"]
    return refresh(root, payload["source_id"], observe=payload["observe"])


def repair(root, source_id, recipe_path):
    from .schema import load

    sources, commands = Sources(root), Commands(root)
    source = sources.get(source_id)
    recipe_path = Path(recipe_path).resolve()
    recipe = load(recipe_path)
    if repository(recipe.source.repository) != source["repository"]:
        raise ValueError("Repair recipe belongs to another source")
    with sources.transaction() as db:
        current = db.execute("SELECT * FROM revisions WHERE source=? AND sha=?", (source_id, source["observed"])).fetchone()
        if current:
            for key in ("job", "run_job"):
                if current[key] and commands.query(current[key])["status"] not in TERMINAL:
                    raise ValueError("Wait for or cancel active admission before repairing")
            sources.event(db, source_id, "repair_requested", {"previous": dict(current), "recipe": str(recipe_path)})
            db.execute("UPDATE revisions SET status='observed',job=NULL,run_job=NULL,release=NULL,error=NULL,attempt=attempt+1 WHERE source=? AND sha=?", (source_id, source["observed"]))
        db.execute("UPDATE sources SET recipe=?,next_poll=0 WHERE id=?", (str(recipe_path), source_id))
    return sources.get(source_id)
