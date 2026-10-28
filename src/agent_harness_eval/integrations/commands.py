"""CLI ingress for recipe builds; all execution is owned by the local Manager."""

import json
import shutil
from pathlib import Path
from uuid import uuid4

from ..provenance import digest, write_json


def submit(root, recipe_path, source=None, *, key=None, allow_fetch=False):
    from ..operations.commands import Commands
    from .builder import git
    from .schema import load

    recipe_path = Path(recipe_path).resolve()
    recipe = load(recipe_path)
    fetch = source is None
    if fetch:
        from .catalog import RECIPES

        if (recipe_path.parent != RECIPES.resolve() and not allow_fetch) or not recipe.source.commit:
            raise ValueError("Automatic source fetch currently requires a pinned builtin recipe")
        sha = recipe.source.commit
    else:
        source = Path(source).resolve()
        if git(source, "status", "--porcelain", "--untracked-files=all").strip():
            raise ValueError("Dirty upstream cannot be admitted")
        sha = git(source, "rev-parse", "HEAD^{commit}").decode().strip()
        if recipe.source.ref and not recipe.source.commit:
            selected = git(source, "rev-parse", "--verify", recipe.source.ref + "^{commit}").decode().strip()
            if selected != sha:
                raise ValueError("Checkout differs from selected ref")
        if recipe.source.commit and recipe.source.commit != sha:
            raise ValueError("Checkout differs from pinned recipe")
    recipe = recipe.model_copy(update={"source": recipe.source.model_copy(update={"commit": sha})})
    commands = Commands(root)
    if key is not None:
        import re
        from ..workspace.models import identity

        if not re.fullmatch(r"[0-9a-f]{64}", key):
            raise ValueError("Build key must be a canonical digest")
        try:
            previous = commands.query(identity({"operation_key": "build:" + key}))
        except KeyError:
            pass
        else:
            if load(Path(previous["payload"]["inputs"]) / "recipe.json").digest != recipe.digest:
                raise ValueError("Build key conflicts with a different recipe")
            return previous
    key = key or uuid4().hex
    if fetch:
        source = commands.store.directory / "upstreams" / key
    inputs = commands.store.directory / "integration-inputs" / key
    inputs.mkdir(parents=True, exist_ok=True)
    hashes = {}
    for name in recipe.patches:
        original = recipe_path.parent / name
        if original.is_symlink() or not original.resolve().is_relative_to(recipe_path.parent):
            raise ValueError("Patch escapes recipe directory")
        target = inputs / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and digest(target) != digest(original):
            raise ValueError("Interrupted frozen build input differs")
        if not target.exists():
            shutil.copy2(original, target)
        hashes[name] = digest(target)
    if (inputs / "recipe.json").exists() and load(inputs / "recipe.json").digest != recipe.digest:
        raise ValueError("Interrupted recipe input differs")
    write_json(inputs / "recipe.json", recipe.model_dump(mode="json"))
    hashes["recipe.json"] = digest(inputs / "recipe.json")
    return commands.submit("integration", {"source": str(source), "fetch": fetch, "inputs": str(inputs), "hashes": hashes,
        "output": str(commands.store.directory / "builds" / key)}, key="build:" + key)


def perform(root, job_id):
    import signal
    from ..operations.commands import Commands
    from .schema import Recipe
    from ..operations.worker import is_managed_worker
    from .builder import build
    from .store import Releases

    if not is_managed_worker():
        raise ValueError("Integration build requires Manager ownership")
    job = Commands(root).query(job_id)
    payload = job["payload"]
    inputs = Path(payload["inputs"])
    for name, expected in payload["hashes"].items():
        if digest(inputs / name) != expected:
            raise ValueError("Frozen build inputs changed")
    def cancelled(*_):
        raise KeyboardInterrupt("Build cancelled")
    signal.signal(signal.SIGTERM, cancelled)
    recipe = Recipe.model_validate(json.loads((inputs / "recipe.json").read_text()))
    if payload.get("fetch"):
        from .checkout import fetch_source

        fetch_source(recipe.source, Path(payload["source"]), inputs / "fetch.log")
    receipt = build(recipe, payload["source"], payload["output"], recipe_directory=inputs)
    Releases(root).add(payload["output"])
    return receipt


def register(sub):
    p = sub.add_parser("cycle", help="有界源码/PR 合并复评闭环")
    p.add_argument("action", choices=["create", "list", "refresh", "stop"])
    p.add_argument("--root", type=Path, default=Path.cwd())
    p.add_argument("--source-id")
    p.add_argument("--candidate")
    p.add_argument("--evaluation", type=Path)
    p.add_argument("--cycle-id")
    p.add_argument("--max-rounds", type=int, default=1)
    p.add_argument("--max-attempts", type=int, default=20)
    p.add_argument("--max-seconds", type=int, default=86400)
    p.add_argument("--confirm-paid", action="store_true")
    p = sub.add_parser("cycle-work", help="内部闭环 Worker")
    p.add_argument("job")
    p.add_argument("--root", type=Path, required=True)
    p = sub.add_parser("source", help="跟踪仓库、观察版本并执行准入")
    p.add_argument("action", choices=["add", "list", "refresh", "revisions", "repair", "assist", "pause", "resume"])
    p.add_argument("--confirm-paid", action="store_true")
    p.add_argument("--root", type=Path, default=Path.cwd())
    p.add_argument("--repository")
    p.add_argument("--executor", choices=["opencode", "dsh", "hermes"])
    p.add_argument("--model-id")
    p.add_argument("--ref", default="main")
    p.add_argument("--recipe", type=Path)
    p.add_argument("--source-id")
    p = sub.add_parser("integration", help="标准 Agent 配方检查、隔离构建与版本目录")
    p.add_argument("action", choices=["validate", "build", "list", "agent", "check", "builtins", "disable", "enable"])
    p.add_argument("--root", type=Path, default=Path.cwd())
    p.add_argument("--recipe", type=Path)
    p.add_argument("--builtin", help="内置 Agent 配方 ID；未指定源码时由 Manager 拉取固定版本")
    p.add_argument("--source", type=Path)
    p.add_argument("--release")
    p.add_argument("--run", type=Path)
    p = sub.add_parser("integration-work", help="内部构建 Worker")
    p.add_argument("job")
    p.add_argument("--root", type=Path, required=True)
    p = sub.add_parser("source-work", help="内部来源跟踪 Worker")
    p.add_argument("job")
    p.add_argument("--root", type=Path, required=True)
    p = sub.add_parser("integration-assist-work", help="内部接入 Agent Worker")
    p.add_argument("job")
    p.add_argument("--root", type=Path, required=True)


def dispatch(args):
    from .store import Releases
    from .schema import load
    from ..operations.commands import Commands

    if args.command in {"cycle", "cycle-work"}:
        from ..rsi import cycle
        from ..operations.manager import ensure

        if args.command == "cycle-work":
            return cycle.perform(args.root, args.job)
        if args.action == "list":
            return cycle.list_cycles(args.root)
        if args.action == "stop":
            return cycle.stop(args.root, args.cycle_id or "")
        if args.action == "create":
            if not args.evaluation:
                raise ValueError("--evaluation is required")
            value = cycle.create(args.root, args.source_id or "", args.evaluation, candidate=args.candidate,
                max_rounds=args.max_rounds, max_attempts=args.max_attempts, max_seconds=args.max_seconds, confirm_paid=args.confirm_paid)
            ensure(args.root, allow_live=args.confirm_paid)
            return {"cycle": value, "job": cycle.queue(args.root, value["id"])}
        ensure(args.root)
        return cycle.queue(args.root, args.cycle_id or "")

    if args.command == "source":
        from .sources import Sources
        from .tracking import queue_refresh, repair
        from ..operations.manager import ensure

        sources = Sources(args.root)
        if args.action == "list":
            return sources.list()
        if args.action == "add":
            source = sources.add(args.repository or "", args.ref, args.recipe)
            ensure(args.root)
            return {"source": source, "job": queue_refresh(args.root, source["id"])}
        sources.get(args.source_id or "")
        if args.action in {"pause", "resume"}:
            return sources.enable(args.source_id, args.action == "resume")
        if args.action == "assist":
            from .assistant import submit as assist

            if not args.confirm_paid:
                raise ValueError("Integration Agent requires --confirm-paid")
            if not args.executor:
                raise ValueError("Integration Agent requires --executor")
            ensure(args.root, allow_live=True)
            return assist(args.root, args.source_id, executor=args.executor, model_id=args.model_id, confirm_paid=args.confirm_paid)
        if args.action == "revisions":
            return sources.revisions(args.source_id)
        if args.action == "repair":
            if not args.recipe:
                raise ValueError("--recipe is required")
            repair(args.root, args.source_id, args.recipe)
            sources.enable(args.source_id, True)
        ensure(args.root)
        return queue_refresh(args.root, args.source_id)
    if args.command == "integration-assist-work":
        from .assistant import perform as assist

        return assist(args.root, args.job)
    if args.command == "source-work":
        from .tracking import perform as refresh_source

        return refresh_source(args.root, args.job)
    if args.command == "integration-work":
        return perform(args.root, args.job)
    from .catalog import disable, list_builtins, recipe_path

    if args.builtin:
        if args.recipe:
            raise ValueError("Choose --builtin or --recipe")
        args.recipe = recipe_path(args.builtin)
    if args.action == "builtins":
        return list_builtins(args.root)
    if args.action in {"disable", "enable"}:
        return disable(args.root, args.release or "", disabled=args.action == "disable")
    if args.action == "validate":
        if not args.recipe:
            raise ValueError("--recipe is required")
        recipe = load(args.recipe)
        return {"recipe_digest": recipe.digest, "recipe": recipe.model_dump(mode="json")}
    if args.action == "list":
        return Releases(args.root).list()
    if args.action == "agent":
        return Releases(args.root).agent(args.release or "")
    if args.action == "check":
        from .conformance import check

        catalog = Releases(args.root)
        catalog.agent(args.release or "")
        if not args.run:
            raise ValueError("--run is required")
        return check(catalog.directory / args.release, args.run)
    if not args.recipe or not args.source and not args.builtin:
        raise ValueError("Use --builtin, or provide --recipe and --source")
    from ..operations.manager import ensure, status
    from ..operations.submission import wait

    current = status(args.root)
    if not current["alive"]:
        ensure(args.root)
    job = submit(args.root, args.recipe, args.source)
    return wait(Commands(args.root), job["id"])
