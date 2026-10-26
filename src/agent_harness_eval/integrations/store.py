"""Immutable release registry. Admission and recommendation live outside releases."""

import json
import shutil
from pathlib import Path
from uuid import uuid4

from ..provenance import digest
from ..workspace.models import AgentRelease, identity
from .schema import Recipe


def verify(directory):
    directory = Path(directory)
    receipt = json.loads((directory / "provenance.json").read_text())
    release = AgentRelease.model_validate(receipt["release"])
    recipe = Recipe.model_validate(receipt["recipe"])
    if (identity(release) != receipt["release_id"] or release.recipe_digest != recipe.digest
            or digest(directory / "agent.tar") != release.artifact_digests["bundle"]):
        raise ValueError("Release identity or artifact digest mismatch")
    return receipt


class Releases:
    def __init__(self, root):
        self.directory = Path(root).resolve() / ".workspace" / "releases"
        self.directory.mkdir(parents=True, exist_ok=True)

    def add(self, built):
        from ..operations.manager import exclusive

        built = Path(built)
        receipt = verify(built)
        target = self.directory / receipt["release_id"]
        with exclusive(self.directory / "catalog.lock"):
            if target.exists():
                if verify(target)["release"] != receipt["release"]:
                    raise ValueError("Existing release conflicts")
                return target
            temporary = self.directory / (".incoming-" + uuid4().hex)
            temporary.mkdir()
            for name in ("agent.tar", "provenance.json", "recipe.json"):
                shutil.copy2(built / name, temporary / name)
            verify(temporary)
            temporary.rename(target)
        return target

    def list(self):
        return [verify(path) for path in sorted(self.directory.iterdir()) if path.is_dir() and not path.name.startswith(".")]

    def agent(self, release_id):
        if len(release_id) != 64 or any(c not in "0123456789abcdef" for c in release_id):
            raise ValueError("Invalid release id")
        path = self.directory / release_id
        receipt = verify(path)
        return {"id": receipt["release"]["agent_id"], "implementation": "agent_harness_eval.agents.recipe:Recipe",
                "version": release_id, "kwargs": {"bundle": str(path / "agent.tar"), "provenance": str(path / "provenance.json")},
                "capabilities": receipt["recipe"]["capabilities"]}
