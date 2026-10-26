"""CLI ingress for recipe builds; all execution is owned by the local Manager."""

import json
import shutil
from pathlib import Path
from uuid import uuid4

from ..provenance import digest, write_json


def submit(root, recipe_path, source=None):
    from ..operations.commands import Commands
    from .builder import git
    from .schema import load

    recipe_path = Path(recipe_path).resolve()
    recipe = load(recipe_path)
    fetch = source is None
    if fetch:
        from .catalog import RECIPES

        if recipe_path.parent != RECIPES.resolve() or not recipe.source.commit:
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
    key = uuid4().hex
    if fetch:
        source = commands.store.directory / "upstreams" / key
    inputs = commands.store.directory / "integration-inputs" / key
    inputs.mkdir(parents=True)
    hashes = {}
    for name in recipe.patches:
        original = recipe_path.parent / name
        if original.is_symlink() or not original.resolve().is_relative_to(recipe_path.parent):
            raise ValueError("Patch escapes recipe directory")
        target = inputs / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(original, target)
        hashes[name] = digest(target)
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


def dispatch(args):
    from .store import Releases
    from .schema import load
    from ..operations.commands import Commands

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
