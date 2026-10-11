"""An independently named adapter enters the runner without editing its registry."""

import json
import sys
from types import ModuleType

from agent_harness_eval.config import Experiment
from agent_harness_eval.run import execute


def test_third_party_adapter_receives_common_contract(tmp_path, monkeypatch):
    module = ModuleType("external_fixture")
    module.Agent = type(
        "Agent", (), {"supported_model_modes": {"mock"}, "accepts_model_config": True}
    )
    monkeypatch.setitem(sys.modules, "external_fixture", module)
    task = tmp_path / "task"
    task.mkdir()
    (task / "task.toml").write_text('schema_version = "1.4"\n')
    exp = Experiment.model_validate(
        {
            "name": "ext",
            "model": {"mode": "mock"},
            "benchmark": {"id": "fixture", "tasks": [str(task)]},
            "agents": [
                {
                    "id": "third-party",
                    "implementation": "external_fixture:Agent",
                    "version": "2",
                }
            ],
        }
    )
    captured = []

    class FakeJob:
        @classmethod
        async def create(cls, config):
            captured.append(config)
            return cls()

        async def run(self):
            pass

    monkeypatch.setattr("agent_harness_eval.run.Job", FakeJob)
    execute(exp, tmp_path / "run")
    cfg = captured[0].agents[0]
    assert cfg.import_path == "external_fixture:Agent"
    assert cfg.kwargs["model_config"]["mode"] == "mock" and cfg.kwargs["version"] == "2"
    result = json.loads((tmp_path / "run/report.json").read_text())
    assert result["agents"]["third-party"]["missing"] == 1
