import json
import subprocess
import time

import pytest

from agent_harness_eval.integrations.sources import Sources
from agent_harness_eval.integrations.tracking import refresh, queue_refresh, repair
from agent_harness_eval.operations.commands import Commands
from test_release_build import repository
from test_integration_recipe import fixture_recipe


def git(path, *args):
    return subprocess.check_output(["git", "-C", str(path), *args], text=True).strip()


def test_concurrent_first_registration_has_one_source_and_one_event(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    def register(_):
        return Sources(tmp_path).add("https://github.com/Fixture/Agent", "main")["id"]
    with ThreadPoolExecutor(8) as pool:
        identities = list(pool.map(register, range(20)))
    assert len(set(identities)) == 1
    sources = Sources(tmp_path)
    assert len(sources.list()) == 1
    with sources.transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM source_events").fetchone()[0] == 1


def push(path, value):
    (path / "agent.py").write_text("VALUE = " + str(value) + "\n")
    git(path, "add", ".")
    git(path, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.com", "commit", "-qm", "update")
    return git(path, "rev-parse", "HEAD")


@pytest.fixture
def source(tmp_path):
    repo = repository(tmp_path)
    recipe = tmp_path / "recipe.json"
    recipe.write_text(json.dumps(fixture_recipe(source={"repository": str(repo), "ref": "main"})))
    root = tmp_path / "platform"
    sources = Sources(root)
    row = sources.add(str(repo), recipe=recipe)
    return sources, row, repo, recipe


def test_same_sha_is_idempotent_and_three_updates_coalesce_before_execution(source):
    sources, row, repo, _ = source
    key, root = row["id"], sources.root
    sha_a = git(repo, "rev-parse", "HEAD")
    refresh(root, key)
    refresh(root, key)
    commands = Commands(root)
    assert len(commands.list()) == 1
    sha_b = push(repo, 2)
    refresh(root, key)
    sha_c = push(repo, 3)
    refresh(root, key)
    assert sources.get(key)["observed"] == sha_c
    assert sources.get(key)["admitted"] is None
    assert [r["sha"] for r in sources.revisions(key)] == [sha_a, sha_b, sha_c]
    assert [r["status"] for r in sources.revisions(key)] == ["superseded", "superseded", "building"]
    assert sum(j["status"] == "queued" for j in commands.list()) == 1
    assert len(commands.list()) == 3
    assert Sources(root).get(key)["observed"] == sha_c


def test_force_push_deleted_ref_and_offline_backoff_preserve_previous_evidence(source):
    sources, row, repo, _ = source
    key, root = row["id"], sources.root
    first = git(repo, "rev-parse", "HEAD")
    refresh(root, key)
    push(repo, 2)
    refresh(root, key)
    git(repo, "reset", "--hard", first)
    refresh(root, key)
    assert sources.get(key)["observed"] == first
    assert sources.revisions(key)[0]["attempt"] == 1
    git(repo, "checkout", "--detach")
    git(repo, "branch", "-D", "main")
    refresh(root, key)
    assert sources.get(key)["observed"] == first
    assert sources.get(key)["error"]
    assert sources.get(key)["next_poll"] > time.time()
    before = sources.get(key)["failures"]
    def offline(_):
        raise OSError("offline fixture")
    refresh(root, key, resolver=offline)
    assert sources.get(key)["failures"] == before + 1
    assert len(sources.revisions(key)) == 2


def test_unknown_repository_needs_recipe_without_calling_a_model(tmp_path):
    sources = Sources(tmp_path)
    row = sources.add("https://github.com/fixture-owner/custom-agent")
    refresh(tmp_path, row["id"], resolver=lambda _: "a" * 40)
    assert sources.revisions(row["id"])[0]["status"] == "needs_recipe"
    assert not Commands(tmp_path).list()
    assert row["repository"].endswith(".git")
    with pytest.raises(ValueError):
        sources.add("https://secret@github.com/owner/repo")


def test_queue_refresh_and_explicit_repair_use_distinct_bounded_attempts(source):
    sources, row, _, recipe = source
    key, root = row["id"], sources.root
    a = queue_refresh(root, key)
    assert queue_refresh(root, key)["id"] == a["id"]
    refresh(root, key)
    revision = sources.revisions(key)[0]
    with pytest.raises(ValueError, match="active"):
        repair(root, key, recipe)
    Commands(root).control(revision["job"], "cancel")
    repair(root, key, recipe)
    refresh(root, key)
    current = sources.revisions(key)[0]
    assert current["attempt"] == 1 and current["job"] != revision["job"]
    assert Commands(root).query(revision["job"])["status"] == "cancelled"
