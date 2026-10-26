import pytest

from agent_harness_eval.integrations.schema import Recipe, expand


def fixture_recipe(**updates):
    return {"schema_version": 1, "id": "fixture", "source": {"repository": "https://example.com/agent.git", "ref": "main"},
            "build": {"image": "python@sha256:" + "a" * 64, "argv": ["python3", "build.py"]},
            "entrypoint": {"argv": ["python3", "{agent_dir}/agent.py", "{request_file}"]}, **updates}


def test_recipe_rejects_ambiguous_execution_and_embedded_credentials():
    recipe = Recipe.model_validate(fixture_recipe())
    assert expand(recipe.entrypoint.argv, {"agent_dir": "/agent", "request_file": "/request.json"}) == ["python3", "/agent/agent.py", "/request.json"]
    assert Recipe.model_validate(fixture_recipe(build={"image": "sha256:" + "b" * 64, "argv": ["python3"]})).build.image.startswith("sha256:")
    with pytest.raises(ValueError):
        Recipe.model_validate(fixture_recipe(build={"image": "python:latest", "argv": ["python3"]}))
    with pytest.raises(ValueError):
        Recipe.model_validate(fixture_recipe(entrypoint={"argv": ["{unknown}"]}))
    with pytest.raises(ValueError):
        Recipe.model_validate(fixture_recipe(environment={"API_KEY": "private-value"}))
    with pytest.raises(ValueError):
        Recipe.model_validate(fixture_recipe(patches=["../escape.patch"]))


def test_python_protocol_uses_same_request_contract():
    recipe = Recipe.model_validate(fixture_recipe(entrypoint={"python": "agent:execute"}))
    assert recipe.entrypoint.python == "agent:execute"
    with pytest.raises(ValueError):
        Recipe.model_validate(fixture_recipe(entrypoint={"python": "agent:execute", "argv": ["agent"]}))
