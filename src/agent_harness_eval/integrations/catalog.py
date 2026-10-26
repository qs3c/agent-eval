"""Independent builtin availability, derived from releases and check evidence."""

import json
from pathlib import Path

from .schema import load
from .store import Releases, verify
from ..workspace.models import identity

RECIPES = Path(__file__).with_name("recipes")


def recipe_path(name):
    available = {path.stem: path for path in RECIPES.glob("*.yaml")}
    if name not in available:
        raise ValueError("Unknown builtin agent: " + name)
    return available[name]


def list_builtins(root):
    catalog = Releases(root)
    releases, invalid = [], []
    for directory in sorted(catalog.directory.iterdir()):
        if not directory.is_dir() or directory.name.startswith("."):
            continue
        try:
            releases.append(verify(directory))
        except (ValueError, OSError, KeyError):
            invalid.append(directory.name)
    result = []
    for path in sorted(RECIPES.glob("*.yaml")):
        recipe = load(path)
        matching = [row for row in releases if row["release"]["agent_id"] == recipe.id
                    and row["release"]["recipe_digest"] == recipe.digest]
        entries = []
        for row in matching:
            directory = catalog.directory / row["release_id"]
            if (directory / "disabled.json").exists():
                continue
            checks, invalid_checks = [], []
            for file in sorted((directory / "checks").glob("*.json")):
                try:
                    value = json.loads(file.read_text())
                    digest = identity({key: data for key, data in value.items() if key != "check_id"})
                    if value["release_id"] != row["release_id"] or digest != value["check_id"] or digest != file.stem:
                        raise ValueError("Check identity mismatch")
                    checks.append(value)
                except (ValueError, OSError, KeyError):
                    invalid_checks.append(file.name)
            entries.append({"release_id": row["release_id"], "checks": checks, "invalid_checks": invalid_checks})
        runnable = any(check["levels"]["runnable"]["passed"] for row in entries for check in row["checks"])
        result.append({"id": recipe.id, "source": recipe.source.model_dump(mode="json"),
                       "build_image": recipe.build.image,
                       "profile": recipe.profile, "recipe_digest": recipe.digest,
                       "artifact_origin": recipe.build.artifact_origin,
                       "availability": "runnable" if runnable else "unchecked" if entries else "unavailable",
                       "reason": None if runnable else "已构建但尚未通过运行检查" if entries else "尚未安装当前配方的 Release",
                       "releases": entries, "invalid_releases": invalid, "limitations": list(recipe.limitations)})
    return result


def disable(root, release_id, *, disabled=True):
    # Remove from selection while preserving immutable bundles referenced by plans.
    catalog = Releases(root)
    catalog.agent(release_id)
    marker = catalog.directory / release_id / "disabled.json"
    if disabled:
        marker.write_text(json.dumps({"disabled": True}))
    else:
        marker.unlink(missing_ok=True)
    return {"release_id": release_id, "disabled": disabled, "evidence_retained": True}
