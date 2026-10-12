import os
import subprocess
import tarfile

import pytest

from agent_harness_eval.manifests import (
    compact_manifest,
    index_legacy_manifest,
    load_manifest,
    verify_manifest,
)
from agent_harness_eval.provenance import (
    digest,
    source_manifest,
    tree_manifest,
    write_json,
)
from agent_harness_eval.runtime_bundle import bundle_runtime, link_archive


def test_compact_roundtrip_and_legacy_index(tmp_path):
    tasks = {}
    for name in ("a", "b"):
        task = tmp_path / "tasks" / name
        (task / "environment/grader").mkdir(parents=True)
        (task / "environment/grader/python").write_text("shared runtime")
        (task / "instruction.md").write_text(name)
        tasks[name] = tree_manifest(task)
    original = {"experiment": {"name": "test"}, "tasks": tasks}
    write_json(tmp_path / "manifest.json", original)
    before = digest(tmp_path / "manifest.json")
    index_legacy_manifest(tmp_path)
    compact = verify_manifest(tmp_path)
    assert digest(tmp_path / "manifest.json") == before
    assert "files" not in compact["tasks"]["a"]
    assert compact["tasks"]["a"]["sha256"] == tasks["a"]["sha256"]
    assert (
        compact["tasks"]["a"]["inventories"]["runtime"]
        == compact["tasks"]["b"]["inventories"]["runtime"]
    )
    assert len(list((tmp_path / "provenance/inventories").iterdir())) == 3
    original["experiment"]["name"] = "changed"
    write_json(tmp_path / "manifest.json", original)
    assert load_manifest(tmp_path)["experiment"]["name"] == "changed"
    with pytest.raises(ValueError, match="Legacy manifest hash"):
        verify_manifest(tmp_path)


def test_new_manifest_and_corruption_detection(tmp_path):
    task = tmp_path / "task"
    task.mkdir()
    (task / "instruction.md").write_text("do work")
    manifest = compact_manifest(tmp_path, {"tasks": {"a": tree_manifest(task)}})
    write_json(tmp_path / "manifest.json", manifest)
    assert verify_manifest(tmp_path) == manifest
    attachment = next((tmp_path / "provenance/inventories").iterdir())
    attachment.write_text("{}")
    with pytest.raises(ValueError, match="Inventory hash"):
        verify_manifest(tmp_path)


def test_hardlinked_runtime_hash_cache_invalidates(tmp_path, monkeypatch):
    from agent_harness_eval import provenance

    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    (a / "runtime.tar").write_bytes(b"one")
    os.link(a / "runtime.tar", b / "runtime.tar")
    calls = []
    real = provenance.digest
    monkeypatch.setattr(provenance, "digest", lambda p: (calls.append(p), real(p))[1])
    cache = {}
    first = tree_manifest(a, hash_cache=cache)
    assert tree_manifest(b, hash_cache=cache) == first
    assert len(calls) == 1
    (b / "runtime.tar").write_bytes(b"different")
    assert tree_manifest(a, hash_cache=cache) != first
    assert len(calls) == 2


def test_runtime_bundle_reuse_contents_and_changes(tmp_path, monkeypatch):
    import agent_harness_eval.runtime_bundle as bundles

    source = tmp_path / "installed"
    source.mkdir()
    (source / "python").write_text("binary")
    (source / "python").chmod(0o755)
    cache = tmp_path / "cache"
    first = bundle_runtime([("python/bin", source)], cache)
    real = bundles.digest
    monkeypatch.setattr(
        bundles, "digest", lambda p: pytest.fail("Unchanged runtime was rehashed")
    )
    assert bundle_runtime([("python/bin", source)], cache)["sha256"] == first["sha256"]
    monkeypatch.setattr(bundles, "digest", real)
    target = tmp_path / "task/runtime.tar"
    link_archive(first["path"], target)
    with tarfile.open(target) as tar:
        assert tar.extractfile("python/bin/python").read() == b"binary"
        assert tar.getmember("python/bin/python").mode & 0o111
    (source / "python").write_text("updated")
    assert bundle_runtime([("python/bin", source)], cache)["sha256"] != first["sha256"]


def test_source_snapshot_tracks_changes_without_listing_clean_files(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()

    def git(*args):
        return subprocess.check_output(["git", "-C", str(root), *args])

    git("init")
    git("config", "user.name", "Test")
    git("config", "user.email", "test@example.invalid")
    (root / "main.go").write_text("original")
    git("add", ".")
    git("commit", "-m", "base")
    clean = source_manifest(root)
    assert not clean["dirty"] and "files" not in clean
    (root / "main.go").write_text("changed")
    (root / "new.go").write_text("extra")
    evidence = tmp_path / "evidence"
    dirty = source_manifest(root, evidence)
    assert dirty["dirty"] and dirty["source_sha256"] != clean["source_sha256"]
    assert b"changed" in (evidence / "changes.patch").read_bytes()
    with tarfile.open(evidence / "untracked.tar") as tar:
        assert tar.extractfile("new.go").read() == b"extra"
