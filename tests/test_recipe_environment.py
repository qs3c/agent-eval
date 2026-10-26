import asyncio
from types import SimpleNamespace

from agent_harness_eval.agents.recipe import Recipe
from agent_harness_eval.integrations.schema import Recipe as Contract
from test_integration_recipe import fixture_recipe


def test_bundled_runtime_environment_is_scoped_to_agent_not_relay(tmp_path):
    adapter = object.__new__(Recipe)
    adapter.recipe = Contract.model_validate(fixture_recipe(
        environment={"PYTHONHOME": "{agent_dir}/python", "PYTHONPATH": "{agent_dir}/source"}))
    adapter.model_config = {"mode": "mock", "name": "fixture", "max_tokens": 100, "context_window": 1000}
    adapter.timeout_seconds = 60
    adapter.logs_dir = tmp_path
    commands = []
    async def prepare(*args):
        return "/workspace"
    async def upload(*args):
        pass
    async def execute(environment, command, **kwargs):
        commands.append((command, kwargs))
        return SimpleNamespace(return_code=0)
    adapter.prepare = prepare
    adapter.exec_as_agent = execute
    adapter.wrap_model = lambda command: "relay-start && " + command
    asyncio.run(adapter.run("task", SimpleNamespace(upload_file=upload), None))
    command, kwargs = commands[0]
    assert "env" not in kwargs
    assert command.startswith("relay-start && mkdir")
    assert "env PYTHONHOME=/installed-agent/recipe/python PYTHONPATH=/installed-agent/recipe/source" in command
