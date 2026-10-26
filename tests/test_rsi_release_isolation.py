import json
import subprocess

import pytest

from agent_harness_eval.rsi.recipe_build import freeze_recipe, verify_recipe, candidate_build, forbid_glue
from agent_harness_eval.integrations.store import verify
from test_integration_recipe import fixture_recipe
from test_release_build import repository, fake_build


def test_rsi_build_changes_eval_copy_only_and_pins_candidate_source(tmp_path):
    source = repository(tmp_path)
    recipe_path = tmp_path / "recipe.json"
    recipe_path.write_text(json.dumps(fixture_recipe()))
    frozen = freeze_recipe(recipe_path, tmp_path / "frozen")
    recipe_path.write_text("broken mutable input")
    before = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
    output = tmp_path / "build"
    output.mkdir()
    receipt = candidate_build(frozen, source, output, executor=fake_build)
    assert receipt == verify(output / "release")
    assert receipt["release"]["source"]["commit"] == before
    assert subprocess.check_output(["git", "-C", str(source), "status", "--porcelain"]) == b""
    assert not (source / "dist").exists()
    assert (output / "release/eval/dist").exists()


def test_frozen_patch_corruption_and_product_glue_are_rejected(tmp_path):
    recipe_path = tmp_path / "recipe.json"
    recipe_path.write_text(json.dumps(fixture_recipe(patches=["glue.patch"])))
    (tmp_path / "glue.patch").write_text("--- /dev/null\n+++ b/.harness-eval/driver.py\n@@ -0,0 +1 @@\n+pass\n")
    frozen = freeze_recipe(recipe_path, tmp_path / "frozen")
    with pytest.raises(ValueError, match="评测适配"):
        forbid_glue(tmp_path, frozen, [".harness-eval/driver.py"])
    forbid_glue(tmp_path, frozen, ["agent.py"])
    (frozen.parent / "glue.patch").write_text("changed")
    with pytest.raises(ValueError, match="changed"):
        verify_recipe(frozen)


def test_registry_bundle_cannot_be_used_to_claim_source_improvement(tmp_path):
    recipe_path = tmp_path / "recipe.json"
    data = fixture_recipe()
    data["build"]["artifact_origin"] = "registry-package"
    recipe_path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="source-built"):
        freeze_recipe(recipe_path, tmp_path / "frozen")
