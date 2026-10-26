from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

from .catalog import list_builtins, recipe_path, disable
from .commands import submit
from .conformance import check
from .store import Releases


class BuildInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    builtin: str | None = None
    recipe: str | None = None
    source: str | None = None


class CheckInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    release_id: str
    run_id: str


def router(console):
    routes = APIRouter(prefix="/api")

    @routes.get("/integrations")
    def overview():
        catalog = Releases(console.root)
        releases = []
        from .store import verify

        for path in sorted(catalog.directory.iterdir()):
            if not path.is_dir() or path.name.startswith(".") or (path / "disabled.json").exists():
                continue
            try:
                row = verify(path)
                releases.append({"release_id": row["release_id"], "release": row["release"], "recipe": row["recipe"]})
            except (ValueError, OSError, KeyError):
                continue
        return {"builtins": list_builtins(console.root), "releases": releases}

    @routes.post("/integrations/build", status_code=202)
    def build(value: BuildInput):
        if bool(value.builtin) == bool(value.recipe):
            raise ValueError("选择内置 Agent，或提供自己的配方与源码目录")
        if value.recipe and not value.source:
            raise ValueError("自定义配方需要本机干净的源码目录")
        path = recipe_path(value.builtin) if value.builtin else Path(value.recipe)
        return submit(console.root, path, value.source)

    @routes.post("/integrations/check")
    def check_run(value: CheckInput):
        catalog = Releases(console.root)
        catalog.agent(value.release_id)
        run = console.detail(value.run_id)
        if run["status"] not in {"completed", "failed", "interrupted", "cancelled"}:
            raise ValueError("请先完成本次接入检查实验")
        return check(catalog.directory / value.release_id, Path(console.row(value.run_id)["path"]))

    @routes.post("/integrations/{release_id}/disable")
    def disable_release(release_id: str):
        return disable(console.root, release_id)

    @routes.post("/runs/preflight")
    def preflight(value: dict):
        return console.preflight(value)

    return routes
