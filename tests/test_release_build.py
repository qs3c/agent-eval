import subprocess

import pytest

from agent_harness_eval.integrations.builder import build
from agent_harness_eval.integrations.schema import Recipe
from test_integration_recipe import fixture_recipe


def repository(tmp_path):
    root = tmp_path / "upstream"
    root.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "remote", "add", "origin", "https://example.com/agent.git"], check=True)
    (root / "agent.py").write_text("VALUE = 1\n")
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(["git", "-C", str(root), "-c", "user.name=Fixture", "-c", "user.email=fixture@example.com", "commit", "-qm", "fixture"], check=True)
    return root


def fake_build(recipe, directory, log):
    target = directory / recipe.build.output
    target.mkdir()
    (target / "agent.py").write_bytes((directory / "agent.py").read_bytes())
    log.write_text("fixture builder; no actual Docker build")


def test_patches_profile_and_artifacts_define_distinct_release(tmp_path):
    source = repository(tmp_path)
    recipe = Recipe.model_validate(fixture_recipe())
    first = build(recipe, source, tmp_path / "first", recipe_directory=tmp_path, executor=fake_build)
    same = build(recipe, source, tmp_path / "same", recipe_directory=tmp_path, executor=fake_build)
    assert first["release_id"] == same["release_id"]
    patch = tmp_path / "fix.patch"
    patch.write_text("diff --git a/agent.py b/agent.py\n--- a/agent.py\n+++ b/agent.py\n@@ -1 +1 @@\n-VALUE = 1\n+VALUE = 2\n")
    changed = Recipe.model_validate(fixture_recipe(patches=["fix.patch"]))
    second = build(changed, source, tmp_path / "second", recipe_directory=tmp_path, executor=fake_build)
    assert first["release_id"] != second["release_id"]
    assert (source / "agent.py").read_text() == "VALUE = 1\n"
    platform = tmp_path / "platform"
    subprocess.run(["git", "init", "-q", str(platform)], check=True)
    nested = platform / ".workspace/builds/fixture"
    build(changed, source, nested, recipe_directory=tmp_path, executor=fake_build)
    assert (nested / "eval/agent.py").read_text() == "VALUE = 2\n"
    assert not (platform / "agent.py").exists()
    alternate = Recipe.model_validate(fixture_recipe(profile="alternate"))
    third = build(alternate, source, tmp_path / "third", recipe_directory=tmp_path, executor=fake_build)
    assert first["release_id"] != third["release_id"]
    def different_artifact(recipe, directory, log):
        fake_build(recipe, directory, log)
        (directory / recipe.build.output / "agent.py").write_text("different build output")
    fourth = build(recipe, source, tmp_path / "fourth", recipe_directory=tmp_path, executor=different_artifact)
    assert fourth["release"]["recipe_digest"] == first["release"]["recipe_digest"]
    assert fourth["release_id"] != first["release_id"]


def test_dirty_source_and_conflicting_patch_stop_before_executor(tmp_path):
    source = repository(tmp_path)
    recipe = Recipe.model_validate(fixture_recipe())
    (source / "untracked").write_text("dirty")
    with pytest.raises(ValueError, match="Dirty upstream"):
        build(recipe, source, tmp_path / "dirty", recipe_directory=tmp_path, executor=fake_build)
    (source / "untracked").unlink()
    (tmp_path / "bad.patch").write_text("invalid diff")
    recipe = Recipe.model_validate(fixture_recipe(patches=["bad.patch"]))
    with pytest.raises(subprocess.CalledProcessError):
        build(recipe, source, tmp_path / "conflict", recipe_directory=tmp_path, executor=lambda *_: pytest.fail("must not build"))
    assert (source / "agent.py").read_text() == "VALUE = 1\n"


def test_catalog_preserves_old_release_and_rejects_tampering(tmp_path):
    from agent_harness_eval.integrations.store import Releases, verify

    source = repository(tmp_path)
    recipe = Recipe.model_validate(fixture_recipe())
    output = tmp_path / "built"
    receipt = build(recipe, source, output, recipe_directory=tmp_path, executor=fake_build)
    catalog = Releases(tmp_path / "platform")
    stored = catalog.add(output)
    assert catalog.add(output) == stored
    assert catalog.agent(receipt["release_id"])["version"] == receipt["release_id"]
    (output / "agent.tar").write_bytes(b"changed")
    assert verify(stored)["release_id"] == receipt["release_id"]
    with pytest.raises(ValueError, match="digest mismatch"):
        catalog.add(output)


def test_source_and_artifact_links_are_not_materialized(tmp_path):
    import io
    import tarfile
    from agent_harness_eval.integrations.builder import unpack

    for name, kind in (("../outside", tarfile.REGTYPE), ("linked", tarfile.SYMTYPE)):
        data = io.BytesIO()
        with tarfile.open(fileobj=data, mode="w") as archive:
            member = tarfile.TarInfo(name)
            member.type = kind
            member.linkname = "/etc/passwd"
            archive.addfile(member)
        with pytest.raises(ValueError, match="unsupported path or link"):
            unpack(data.getvalue(), tmp_path / "extract")


def test_explicit_internal_source_links_materialize_without_host_links(tmp_path):
    import io
    import tarfile
    from agent_harness_eval.integrations.builder import unpack

    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode="w") as archive:
        regular = tarfile.TarInfo("src/a.txt")
        regular.size = 4
        archive.addfile(regular, io.BytesIO(b"safe"))
        link = tarfile.TarInfo("docs/a.txt")
        link.type = tarfile.SYMTYPE
        link.linkname = "../src/a.txt"
        archive.addfile(link)
    unpack(data.getvalue(), tmp_path, internal_links=True)
    assert (tmp_path / "docs/a.txt").read_bytes() == b"safe"
    assert not (tmp_path / "docs/a.txt").is_symlink()


def test_offline_image_fallback_requires_the_exact_pinned_config_digest(monkeypatch):
    from types import SimpleNamespace
    from agent_harness_eval.integrations.builder import resolve_image

    spec = Recipe.model_validate(fixture_recipe(build={"image": "python@sha256:" + "a" * 64,
        "image_id": "sha256:" + "b" * 64, "argv": ["python3"]})).build
    def inspect(argv, **kwargs):
        return SimpleNamespace(returncode=0 if argv[-1] == spec.image_id else 1, stdout=spec.image_id)
    monkeypatch.setattr(subprocess, "run", inspect)
    assert resolve_image(spec) == spec.image_id
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: SimpleNamespace(returncode=0, stdout="sha256:" + "c" * 64))
    with pytest.raises(ValueError, match="digest mismatch"):
        resolve_image(spec)
