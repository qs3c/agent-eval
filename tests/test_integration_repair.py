import json

import pytest

from agent_harness_eval.integrations.assistant import accept
from agent_harness_eval.integrations.sources import Sources
from agent_harness_eval.integrations.tracking import refresh
from agent_harness_eval.operations.commands import Commands
from test_source_tracking import source, git, push


def proposal(source, tmp_path):
    sources, row, repo, original = source
    sources.observe(row["id"], git(repo, "rev-parse", "HEAD"))
    row = sources.get(row["id"])
    recipe = json.loads(original.read_text())
    recipe["source"]["commit"] = row["observed"]
    result = tmp_path / "result.json"
    result.write_text(json.dumps({"recipe": recipe, "summary": "补充标准接入入口", "limitations": ["运行检查尚未执行"]}))
    return sources, row, result


def test_proposal_is_frozen_then_checked_not_self_admitted(source, tmp_path):
    sources, row, result = proposal(source, tmp_path)
    job = accept(sources.root, row, tmp_path, result, tmp_path / "accepted")
    assert job["kind"] == "source-refresh"
    assert Sources(sources.root).get(row["id"])["admitted"] is None
    frozen = tmp_path / "accepted/recipe.json"
    expected = frozen.read_bytes()
    result.write_text("not a recipe anymore")
    refresh(sources.root, row["id"], observe=False)
    assert sources.revisions(row["id"])[0]["status"] == "building"
    assert frozen.read_bytes() == expected
    assert len([j for j in Commands(sources.root).list() if j["kind"] == "integration"]) == 1


def test_proposal_for_stale_source_does_not_replace_current_recipe(source, tmp_path):
    sources, row, result = proposal(source, tmp_path)
    sources.observe(row["id"], push(source[2], 42))
    with pytest.raises(ValueError, match="源码已更新"):
        accept(sources.root, row, tmp_path, result, tmp_path / "accepted")
    assert sources.get(row["id"])["recipe"] == str(source[3])
    assert not (tmp_path / "accepted").exists()


@pytest.mark.parametrize("change", ["repository", "commit", "artifact", "patch"])
def test_invalid_generated_proposals_cannot_change_source_state(source, tmp_path, change):
    sources, row, result = proposal(source, tmp_path)
    value = json.loads(result.read_text())
    if change == "repository":
        value["recipe"]["source"]["repository"] = "https://github.com/other/repo"
    elif change == "commit":
        value["recipe"]["source"]["commit"] = "f" * 40
    elif change == "artifact":
        value["recipe"]["build"]["artifact_origin"] = "registry-package"
    else:
        value["recipe"]["patches"] = ["../outside.patch"]
    result.write_text(json.dumps(value))
    with pytest.raises(ValueError):
        accept(sources.root, row, tmp_path, result, tmp_path / "accepted")
    assert not Commands(sources.root).list()
    assert sources.get(row["id"])["admitted"] is None
