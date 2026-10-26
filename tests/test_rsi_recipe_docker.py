"""No-model product patch -> recipe build -> paired Harbor/targeted validation."""
import json
import os
import subprocess
import shutil
import sys
from pathlib import Path

import pytest
from test_rsi import create, operate, setup, git

from agent_harness_eval.guardian.evidence import read, write
from agent_harness_eval.rsi.publication import readiness
from test_integration_recipe import fixture_recipe

pytestmark = pytest.mark.skipif(os.environ.get("RSI_DOCKER_TESTS") != "1", reason="opt-in recipe RSI Docker validation")


@pytest.mark.parametrize("holdout_regresses", [False, True])
def test_product_only_candidate_is_built_and_replayed_with_same_targeted_controls(setup, monkeypatch, holdout_regresses):
    store, case, source, _, profile = setup
    home = store.project / "isolated-home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    (source / "agent.py").write_text('''import json,sys
from pathlib import Path
from harness import VALUE
r=json.loads(Path(sys.argv[1]).read_text())
Path(r["workspace"],"result.txt").write_text("ready\\n" if VALUE else "bug\\n")
Path(r["output_dir"],"agent-result.json").write_text(json.dumps({"schema_version":1,"status":"completed"}))
''')
    (source / "build.py").write_text("from pathlib import Path\nimport shutil\nPath('dist').mkdir()\nfor f in ['agent.py','harness.py']: shutil.copy2(f,'dist/'+f)\n")
    git(source, "add", ".")
    git(source, "commit", "-m", "fixture adapter")
    image = json.loads(subprocess.check_output(["docker", "image", "inspect", "python:3.12.13-slim-bookworm", "--format", "{{json .RepoDigests}}"], text=True))[0]
    recipe = fixture_recipe(id="a", source={"repository": str(source), "ref": "main"},
        build={"image": image, "argv": ["python3", "build.py"]}, model_modes=["offline"], model_protocol="none")
    write(store.project / "recipe.json", recipe)
    project = Path(__file__).parents[1]
    exp = json.loads((project / "experiments/controls.json").read_text())
    exp["agents"] = [{"id": "a", "implementation": "nop", "version": "historical"}]
    exp["benchmark"]["tasks"] = [str(project / "tasks/file-create"), str(project / "tasks/file-preserve")]
    write(store.project / "evaluation.json", exp)
    profile.update(integration_recipe=str(store.project / "recipe.json"), evaluation=str(store.project / "evaluation.json"))
    held_task = store.project / "heldout-task"
    shutil.copytree(project / "tasks/file-create", held_task)
    (held_task / "instruction.md").write_text("Independent deterministic holdout fixture; not a model benchmark.\n")
    if holdout_regresses:
        check = held_task / "tests/test.sh"
        check.write_text(check.read_text().replace("ready", "bug"))
    holdout = json.loads(json.dumps(exp))
    holdout["benchmark"]["tasks"] = [str(held_task)]
    write(store.project / "holdout.json", holdout)
    profile.update(evaluation_holdout=str(store.project / "holdout.json"), required_validation=["build", "targeted", "benchmark-replay", "holdout"])
    for name, condition in (("targeted_reproducer", "VALUE == 1"), ("targeted_negative_control", "VALUE in (0, 1)")):
        code = "import json,sys,pathlib;exec(pathlib.Path('harness.py').read_text());ok=" + condition + ";pathlib.Path(sys.argv[1],'assertion.json').write_text(json.dumps({'status':'passed' if ok else 'assertion_failed','assertion':" + repr(condition) + "}));sys.exit(0 if ok else 1)"
        profile[name] = {"argv": [sys.executable, "-B", "-c", code, "{output}"], "timeout_seconds": 10}
    write(store.project / "rsi-profiles/fixture.json", profile)
    candidate, action = operate(store, create(setup))
    assert action["status"] == "completed", action
    candidate, action = operate(store, candidate, "evaluate")
    assert action["status"] == "completed", action
    assert candidate["validation"]["promotable"] is not holdout_regresses, candidate["validation"]
    assert readiness(store, candidate)[0] is not holdout_regresses
    path = store.path("candidates", candidate["id"])
    changed = git(path / "workspace", "diff", "--name-only", candidate["parent_commit"], candidate["commit"])
    assert changed == "harness.py"
    assert (source / "harness.py").read_text() == "VALUE = 0\n"
    body = (path / "PR.md").read_text()
    assert "中文" in body and "English" in body
    run = path / "evaluations" / candidate["evaluations"][-1]["id"]
    for side in ("parent", "candidate"):
        assert read(run / side / "report.json")["agents"]["a"]["errors"] == 0
        assert (run / (side + "-build/release/provenance.json")).is_file()
