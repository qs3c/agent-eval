import json
import sys
import threading
import types
from pathlib import Path

import pytest

from agent_harness_eval import batching, image_lifecycle, regrading
from agent_harness_eval.guardian.evidence import read


def test_failed_batch_retains_shared_images_until_all_consumers_finish(tmp_path, monkeypatch):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    calls = []
    monkeypatch.setattr(batching, "_cleanup_batch_images", lambda exp, root, paths, n: calls.append(root))
    image_lifecycle.retain(a, [a / "task"], 1)
    image_lifecycle.retain(b, [b / "task"], 1)
    image_lifecycle.collect(None, b, [b / "task"], 1)
    assert calls == []  # a may be failed/offline; no timeout drops its lease.
    assert read(b / "batches/001/cleanup.json")["status"].startswith("deferred")
    image_lifecycle.collect(None, a, [a / "task"], 1)
    assert set(calls) == {a, b}
    assert read(image_lifecycle.registry()) == {}


def test_collection_waits_for_standalone_grading(tmp_path, monkeypatch):
    collecting = threading.Event()
    monkeypatch.setattr(batching, "_cleanup_batch_images", lambda *args: collecting.set())
    image_lifecycle.retain(tmp_path, [], 1)
    with regrading.gate("image-use", shared=True):
        thread = threading.Thread(target=image_lifecycle.collect, args=(None, tmp_path, [], 1))
        thread.start()
        assert not collecting.wait(0.15)
    thread.join(timeout=2)
    assert not thread.is_alive()
    assert collecting.is_set()


def test_corrupt_lease_registry_prevents_collection(tmp_path, monkeypatch):
    image_lifecycle.registry().write_text("broken json")
    monkeypatch.setattr(batching, "_cleanup_batch_images", lambda *args: pytest.fail("must retain images"))
    with pytest.raises(ValueError):
        image_lifecycle.collect(None, tmp_path, [], 1)


def test_incomplete_collection_keeps_ready_lease_for_retry(tmp_path, monkeypatch):
    monkeypatch.setattr(batching, "_cleanup_batch_images", lambda *args: False)
    image_lifecycle.retain(tmp_path, [], 1)
    image_lifecycle.collect(None, tmp_path, [], 1)
    assert image_lifecycle.leases()[image_lifecycle.identity(tmp_path, 1)]["status"] == "ready"
    monkeypatch.setattr(batching, "_cleanup_batch_images", lambda *args: True)
    image_lifecycle.collect(None, tmp_path, [], 1)
    assert image_lifecycle.leases() == {}


@pytest.mark.parametrize("detail,absent", [
    ("Error response from daemon: No such image: missing:latest", True),
    ("Cannot connect to the Docker daemon", False),
    ("permission denied", False),
])
def test_inspect_errors_are_not_all_missing_images(tmp_path, monkeypatch, detail, absent):
    from types import SimpleNamespace

    task = tmp_path / "task"
    task.mkdir()
    (task / "runtime-provenance.json").write_text('{"image": "missing:latest"}')
    def run(cmd, **kwargs):
        if cmd[1:3] == ["image", "inspect"]:
            return SimpleNamespace(returncode=1, stdout="", stderr=detail)
        return SimpleNamespace(returncode=0, stdout="", stderr="")
    monkeypatch.setattr(batching.subprocess, "run", run)
    image_lifecycle.retain(tmp_path, [task], 1)
    if absent:
        image_lifecycle.collect(None, tmp_path, [task], 1)
        assert image_lifecycle.leases() == {}
    else:
        with pytest.raises(RuntimeError, match="inspection failed"):
            image_lifecycle.collect(None, tmp_path, [task], 1)
        assert image_lifecycle.leases()


@pytest.mark.parametrize("pinned", [False, True])
def test_official_adapter_image_lifecycle_and_frozen_ref(tmp_path, monkeypatch, pinned):
    from agent_harness_eval.resources import official_runner

    docker_utils = types.ModuleType("swebench.harness.docker_utils")
    docker_utils.should_remove = lambda *a: True
    docker_utils.clean_images = lambda *a: (_ for _ in ()).throw(AssertionError("global scan"))
    class Spec:
        instance_id = "task"
        @property
        def instance_image_key(self):
            return "swebench/sweb.eval.x86_64.task:latest"
    harness = types.ModuleType("swebench.harness")
    harness.docker_utils = docker_utils
    spec_module = types.ModuleType("swebench.harness.test_spec.test_spec")
    spec_module.TestSpec = Spec
    monkeypatch.setitem(sys.modules, "swebench.harness", harness)
    monkeypatch.setitem(sys.modules, "swebench.harness.test_spec.test_spec", spec_module)
    monkeypatch.chdir(tmp_path)
    expected = "swebench/sweb.eval.x86_64.task:latest"
    if pinned:
        expected = "swebench/sweb.eval.x86_64.task@sha256:" + "a" * 64
        Path("official-image-map.json").write_text(json.dumps({"task": expected}))
    official_runner.configure()
    assert docker_utils.should_remove("image", "none", True, set()) is False
    docker_utils.clean_images(None, set(), "none", True)
    assert Spec().instance_image_key == expected
    assert read(Path("official-images.json")) == [Spec().instance_image_key]
