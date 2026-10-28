import json
from pathlib import Path

import pytest

from agent_harness_eval.integrations import catalog
from agent_harness_eval.integrations.builder import build
from agent_harness_eval.integrations.schema import Recipe, load
from agent_harness_eval.integrations.store import Releases
from test_integration_recipe import fixture_recipe
from test_release_build import repository, fake_build


def test_builtin_recipes_pin_sources_runtime_and_keep_patches_present():
    assert {path.stem for path in catalog.RECIPES.glob("*.yaml")} == {"hermes", "opencode", "dsh"}
    for path in catalog.RECIPES.glob("*.yaml"):
        recipe = load(path)
        assert len(recipe.source.commit) == 40
        assert recipe.build.image.split("sha256:")[1]
        assert all((path.parent / patch).is_file() for patch in recipe.patches)
    with pytest.raises(ValueError, match="Unknown builtin"):
        catalog.recipe_path("../hermes")


def test_dsh_keeps_existing_published_standard_transport_and_dependency_lock():
    recipe = load(catalog.recipe_path("dsh"))
    assert recipe.build.artifact_origin == "registry-package"
    patch = (catalog.RECIPES / "dsh-eval.patch").read_text()
    files, current = {}, None
    for line in patch.splitlines():
        if line.startswith("+++ b/.harness-eval/"):
            current = line.removeprefix("+++ b/.harness-eval/")
            files[current] = []
        elif current and line.startswith("+"):
            files[current].append(line[1:])
    root = Path(__file__).parents[1]
    for name, original in [("eval-driver.mjs", "src/agent_harness_eval/resources/dsh-driver.mjs"),
                           ("package.json", "locks/dsh-package.json"), ("package-lock.json", "locks/dsh-package-lock.json")]:
        assert "\n".join(files[name]).strip() == (root / original).read_text().strip()


def test_uninstalled_and_unchecked_are_not_available_and_disabling_preserves_history(tmp_path, monkeypatch):
    recipes = tmp_path / "recipes"
    recipes.mkdir()
    data = fixture_recipe()
    (recipes / "fixture.yaml").write_text(json.dumps(data))
    monkeypatch.setattr(catalog, "RECIPES", recipes)
    root = tmp_path / "platform"
    assert catalog.list_builtins(root)[0]["availability"] == "unavailable"
    source = repository(tmp_path)
    output = tmp_path / "build"
    receipt = build(Recipe.model_validate(data), source, output, recipe_directory=recipes, executor=fake_build)
    releases = Releases(root)
    releases.add(output)
    assert catalog.list_builtins(root)[0]["availability"] == "unchecked"
    catalog.disable(root, receipt["release_id"])
    assert catalog.list_builtins(root)[0]["availability"] == "unavailable"
    assert releases.agent(receipt["release_id"])["version"] == receipt["release_id"]
    catalog.disable(root, receipt["release_id"], disabled=False)
    assert catalog.list_builtins(root)[0]["availability"] == "unchecked"
    corrupt = releases.directory / ("f" * 64)
    corrupt.mkdir()
    (corrupt / "provenance.json").write_text("invalid")
    result = catalog.list_builtins(root)[0]
    assert result["availability"] == "unchecked"
    assert result["invalid_releases"] == [corrupt.name]
