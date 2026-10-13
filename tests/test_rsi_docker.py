import json
import os
import sys
from pathlib import Path

import pytest
from test_rsi import create, operate, setup  # noqa: F401 - shared pytest fixture

from agent_harness_eval.guardian.evidence import read, write

pytestmark = pytest.mark.skipif(
    sys.platform != "linux" or os.environ.get("RSI_DOCKER_TESTS") != "1",
    reason="opt-in offline Docker acceptance",
)


def test_parent_and_candidate_are_built_and_graded_by_harbor(setup, monkeypatch):  # noqa: F811 - pytest fixture injection
    store, _, _, _, profile = setup
    root = store.project
    adapter = root / "rsi_eval_adapter.py"
    adapter.write_text("""from pathlib import Path
from agent_harness_eval.agents.control import Control
class Agent(Control):
    def __init__(self,*args,binary,**kwargs):
        super().__init__(*args,succeed='VALUE = 1' in Path(binary).read_text(),**kwargs)
""")
    monkeypatch.syspath_prepend(str(root))
    monkeypatch.setenv(
        "PYTHONPATH", str(root) + os.pathsep + os.environ.get("PYTHONPATH", "")
    )
    source = Path(__file__).resolve().parents[1]
    exp = json.loads((source / "experiments/controls.json").read_text())
    exp["agents"] = [
        {
            "id": "a",
            "implementation": "rsi_eval_adapter:Agent",
            "version": "fixture",
            "kwargs": {"binary": "/missing/historical-agent-artifact"},
        }
    ]
    exp["benchmark"]["tasks"] = [
        str(source / "tasks/file-create"),
        str(source / "tasks/file-preserve"),
    ]
    write(root / "evaluation.json", exp)
    profile["evaluation"] = str(root / "evaluation.json")
    profile["build"] = {
        "argv": [
            sys.executable,
            "-c",
            "import shutil,sys;shutil.copyfile('harness.py',sys.argv[1])",
            "{output}/agent.bin",
        ]
    }
    profile["artifacts"] = {"binary": "agent.bin"}
    write(root / "rsi-profiles/fixture.json", profile)
    candidate, action = operate(store, create(setup))
    assert action["status"] == "completed"
    candidate, action = operate(store, candidate, "evaluate")
    assert action["status"] == "completed", action
    evaluation = candidate["evaluations"][-1]
    assert evaluation["assessment"] == "improved", evaluation
    assert evaluation["parent_commit"] == candidate["parent_commit"]
    assert evaluation["candidate_commit"] == candidate["commit"]
    assert evaluation["scores"] == {"parent": 0.0, "candidate": 1.0, "delta": 1.0}
    directory = (
        store.path("candidates", candidate["id"]) / "evaluations" / evaluation["id"]
    )
    for side, commit in (
        ("parent", candidate["parent_commit"]),
        ("candidate", candidate["commit"]),
    ):
        assert (
            read(directory / (side + "-artifacts.json"))["binary"]["commit"] == commit
        )
        assert read(directory / side / "report.json")["agents"]["a"]["recorded"] == 2
