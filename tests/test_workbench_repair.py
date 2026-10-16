import json

import pytest

from agent_harness_eval.run import verify_workbench_repair


def test_resume_still_rejects_code_changes_without_explicit_record():
    with pytest.raises(ValueError, match="differs from the frozen"):
        verify_workbench_repair({"batching.py": "old"}, {"batching.py": "new"}, None)


@pytest.mark.parametrize("mode", ["valid", "wrong_old", "wrong_new", "agent", "added", "reason"])
def test_repair_requires_exact_inventories_and_preserves_agent_code(tmp_path, mode):
    previous = {"batching.py": "old", "agents/bkcrab.py": "unchanged"}
    current = {**previous, "batching.py": "new"}
    if mode == "agent":
        current["agents/bkcrab.py"] = "new-agent"
    if mode == "added":
        current["cli.py"] = "new-file"
    record = {"schema_version": 1, "reason": "修复镜像清理和 uv 启动路径", "previous": previous.copy(), "current": current.copy()}
    if mode == "wrong_old":
        record["previous"]["batching.py"] = "different"
    if mode == "wrong_new":
        record["current"]["batching.py"] = "different"
    if mode == "reason":
        record["reason"] = ""
    path = tmp_path / "repair.json"
    path.write_text(json.dumps(record))
    if mode == "valid":
        assert verify_workbench_repair(previous, current, path) == record
    else:
        with pytest.raises(ValueError, match="inventory mismatch"):
            verify_workbench_repair(previous, current, path)


def test_audited_resume_keeps_frozen_manifest_and_still_checks_tasks(tmp_path, monkeypatch):
    import asyncio
    from pathlib import Path

    from agent_harness_eval import batching, run
    from agent_harness_eval.config import BatchConfig, load
    from agent_harness_eval.manifests import store_inventory
    from agent_harness_eval.provenance import write_json

    exp = load(Path("experiments/controls.json"))
    exp.batch = BatchConfig(size=1, min_free_gib=0)

    async def batches(*args, **kwargs):
        pass

    monkeypatch.setattr(batching, "run_batches", batches)
    root = tmp_path / "run"
    asyncio.run(run.run_experiment(exp, root))
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    current = json.loads((root / manifest["workbench"]["inventory"]["path"]).read_text())
    previous = {**current, "batching.py": "historical-code-hash"}
    manifest["workbench"]["inventory"] = store_inventory(root, previous)
    write_json(manifest_path, manifest)
    original = manifest_path.read_bytes()
    repair = tmp_path / "repair.json"
    write_json(repair, {"schema_version": 1, "reason": "修复历史清理规则", "previous": previous, "current": current})
    with pytest.raises(ValueError, match="differs from the frozen"):
        asyncio.run(run.run_experiment(exp, root, resume=True))
    asyncio.run(run.run_experiment(exp, root, resume=True, workbench_repair=repair))
    assert manifest_path.read_bytes() == original
    saved = list((root / "recovery").glob("*/repair.json"))
    assert len(saved) == 1 and json.loads(saved[0].read_text()) == json.loads(repair.read_text())
    assert (saved[0].parent / "code/batching.py").read_bytes() == Path(batching.__file__).read_bytes()
    (root / "tasks/file-create/instruction.md").write_text("tampered")
    with pytest.raises(ValueError, match="Frozen task"):
        asyncio.run(run.run_experiment(exp, root, resume=True, workbench_repair=repair))
