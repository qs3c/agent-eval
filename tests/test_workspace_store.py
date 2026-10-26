import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from agent_harness_eval.workspace.models import Attempt, ExperimentPlan, RunState, canonical_json, identity
from agent_harness_eval.workspace.store import Store


def plan():
    return ExperimentPlan(experiment={
        "agents": [{"id": "one", "capabilities": ["shell", "filesystem"]}, {"id": "two", "capabilities": []}],
        "benchmark": {"tasks": ["a", "b"], "requires": ["shell", "filesystem"]},
        "model": {"api_key_env": "TEST_KEY", "name": "mock"},
    })


def test_canonical_sets_do_not_destroy_ordered_inputs():
    original = plan()
    edited = json.loads(original.model_dump_json())
    edited["experiment"]["agents"][0]["capabilities"].reverse()
    edited["experiment"]["benchmark"]["requires"].reverse()
    assert identity(original) == identity(ExperimentPlan.model_validate(edited))
    edited["experiment"]["benchmark"]["tasks"].reverse()
    assert identity(original) != identity(ExperimentPlan.model_validate(edited))
    edited = json.loads(original.model_dump_json())
    edited["experiment"]["agents"].reverse()
    assert identity(original) != identity(ExperimentPlan.model_validate(edited))


@pytest.mark.parametrize("value", [{"api_key": "secret"}, {"nested": {"password": "x"}}, {"authorization": "x"}, {"n": float("nan")}])
def test_secrets_and_nonfinite_values_cannot_enter_index(value):
    with pytest.raises(ValueError):
        canonical_json(value)


def test_idempotent_creation_conflict_and_atomic_outbox(tmp_path):
    store = Store(tmp_path)
    with ThreadPoolExecutor(max_workers=4) as workers:
        ids = list(workers.map(lambda _: store.create_plan(plan(), "same-request"), range(8)))
    assert len(set(ids)) == 1
    assert len(store.pending_events()) == 1
    mutated = plan().model_copy(deep=True)
    mutated.experiment["model"]["name"] = "different"
    with pytest.raises(ValueError, match="conflicts"):
        store.create_plan(mutated, "same-request")
    assert store.plan(ids[0]).experiment["model"]["name"] == "mock"
    assert len(store.pending_events()) == 1
    store.acknowledge(store.pending_events()[0]["seq"])
    assert store.pending_events() == []


def test_record_state_is_separate_and_stale_generation_rejected(tmp_path):
    store = Store(tmp_path)
    plan_id = store.create_plan(plan(), "request")
    attempt = Attempt(plan_id=plan_id, agent_id="one", task_id="a", repeat=1)
    key = store.create_record("attempt", attempt)
    assert store.create_record("attempt", attempt) == key
    state = RunState(execution="completed", scoring="pending", evidence="complete", resources="retained")
    assert store.transition("attempt", key, state, generation=0) == 1
    with pytest.raises(ValueError, match="Stale"):
        store.transition("attempt", key, state, generation=0)
    assert identity(store.plan(plan_id)) == identity(plan())
    with store.transaction() as db:
        row = db.execute("SELECT * FROM records WHERE id=?", (key,)).fetchone()
        assert identity(json.loads(row["payload"])) == key
        assert json.loads(row["state"])["scoring"] == "pending"


def test_failed_transaction_does_not_publish_event(tmp_path):
    store = Store(tmp_path)
    with pytest.raises(RuntimeError):
        with store.transaction() as db:
            store.event(db, "test", "id", {})
            raise RuntimeError("crash before commit")
    assert store.pending_events() == []


def test_references_must_exist_and_tampered_plan_is_rejected(tmp_path):
    store = Store(tmp_path)
    with pytest.raises(ValueError, match="unknown plan"):
        store.create_record("attempt", Attempt(plan_id="missing", agent_id="one", task_id="a", repeat=1))
    key = store.create_plan(plan(), "key")
    with store.transaction() as db:
        value = plan().model_copy(deep=True)
        value.experiment["model"]["name"] = "tampered"
        db.execute("UPDATE plans SET payload=? WHERE id=?", (canonical_json(value), key))
    with pytest.raises(ValueError, match="identity mismatch"):
        store.plan(key)
