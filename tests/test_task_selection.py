import shutil
from pathlib import Path

import pytest

from agent_harness_eval.benchmarks.selection import select, materialize


def tasks(tmp_path):
    result = []
    for i in range(6):
        path = tmp_path / ("task-" + str(i))
        shutil.copytree(Path("tasks/file-create"), path)
        result.append(path)
    return result


def test_seed_order_exclusions_and_content_are_frozen(tmp_path):
    paths = tasks(tmp_path)
    first = select(paths, count=3, seed=17, excluded={"task-0"}, dataset={"name": "fixture", "revision": "v1", "split": "test"})
    assert first == select(list(reversed(paths)), count=3, seed=17, excluded={"task-0"}, dataset=first["dataset"])
    assert "task-0" not in first["task_ids"]
    assert len(set(first["task_ids"])) == 3
    selected = materialize(paths, first, tmp_path / "selected")
    assert [p.name for p in selected] == first["task_ids"]
    (paths[1] / "instruction.md").write_text("changed")
    assert select(paths, count=3, seed=17, excluded={"task-0"}, dataset=first["dataset"])["selection_id"] != first["selection_id"]
    with pytest.raises(ValueError, match="changed"):
        materialize(paths, first, tmp_path / "tampered")


def test_selection_rejects_insufficient_candidates_and_duplicate_names(tmp_path):
    paths = tasks(tmp_path)
    with pytest.raises(ValueError, match="count"):
        select(paths, count=7, seed=1)
    with pytest.raises(ValueError, match="Duplicate"):
        select([paths[0], paths[0]], count=1, seed=1)
