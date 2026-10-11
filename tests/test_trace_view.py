import hashlib
import json

import pytest

from agent_harness_eval.console import Console
from agent_harness_eval.trace_view import assessment, decorate, trace, trial_paths


@pytest.fixture
def sample(tmp_path):
    c = Console(tmp_path)
    run = c.artifacts / "sample"
    trial = run / "jobs/dsh-standard/task__abc"
    agent = trial / "agent"
    agent.mkdir(parents=True)
    (trial / "result.json").write_text('{"task_name":"task"}')
    with c.connect() as db:
        db.execute(
            "INSERT INTO runs(id,path,config,status,created,updated) VALUES(?,?,?,?,?,?)",
            ("r", str(run), "{}", "completed", "", ""),
        )

    def write(name, rows):
        (agent / name).write_text("".join(json.dumps(r) + "\n" for r in rows))

    write(
        "native_wire_requests.jsonl", [{"request_id": "one", "body": {"messages": []}}]
    )
    write(
        "wire_responses.jsonl",
        [{"request_id": "one", "usage": {"input_tokens": 100, "output_tokens": 10}}],
    )
    write(
        "wire_chunks.jsonl",
        [
            {
                "request_id": "one",
                "event": {
                    "message": {
                        "usage": {"cache_read_input_tokens": 80, "input_tokens": 20}
                    }
                },
            },
            {"request_id": "one", "event": {"usage": {"output_tokens": 10}}},
        ],
    )

    def e(typ, data):
        return {"data": {"event": {"type": typ, "data": data}}}

    write(
        "events.jsonl",
        [
            e("step/start", {}),
            e(
                "assistant/message",
                {
                    "message": {
                        "content": [
                            {"type": "reasoning", "text": "PRIVATE REASONING"},
                            {"type": "text", "text": "Running tests"},
                        ]
                    }
                },
            ),
            e("tool/call", {"callId": "t", "name": "bash", "arguments": "test"}),
            e(
                "tool/result",
                {
                    "message": {
                        "toolCallId": "t",
                        "content": [
                            {"type": "text", "text": "Ran 3 tests\nFAILED (errors=1)"}
                        ],
                    }
                },
            ),
        ],
    )
    return c, next(iter(trial_paths(c, "r"))), write, trial


def test_trace_usage_and_evidence(sample):
    c, key, _, _ = sample
    result = trace(c, "r", key)
    assert result["metrics"]["cached_tokens"] == 80
    assert result["metrics"]["uncached_tokens"] == 20
    assert result["metrics"]["error_signals"] == 1
    assert "PRIVATE REASONING" not in json.dumps(result)
    assert result["rounds"][0]["tools"][0]["result_source"] == "events.jsonl:4"
    assert result["rounds"][0]["assistant"] == "Running tests"
    with pytest.raises(KeyError):
        trace(c, "r", "../../secrets")


@pytest.mark.parametrize("ids", [[], ["one", "one"], ["different"]])
def test_missing_duplicate_or_wrong_response_unknown(sample, ids):
    c, key, write, _ = sample
    write(
        "wire_responses.jsonl",
        [
            {"request_id": i, "usage": {"input_tokens": 5, "output_tokens": 2}}
            for i in ids
        ],
    )
    result = trace(c, "r", key)
    assert result["usage_complete"] is False
    assert result["metrics"]["input_tokens"] is None
    assert result["rounds"][0]["request_id"] is None
    assert result["warnings"]


def test_missing_cache_is_unknown(sample):
    c, key, write, _ = sample
    write("wire_chunks.jsonl", [])
    result = trace(c, "r", key)
    assert result["metrics"]["input_tokens"] == 100
    assert result["metrics"]["cached_tokens"] is None


def test_review_requires_unchanged_evidence(sample):
    c, _, _, trial = sample
    f = trial / "result.json"
    review = {
        "sample": {
            "dsh-standard": {
                "status": "accepted",
                "reason": "checked",
                "evidence": {
                    str(f.relative_to(c.root)): hashlib.sha256(
                        f.read_bytes()
                    ).hexdigest()
                },
            }
        }
    }
    (c.root / "console_reviews.json").write_text(json.dumps(review))
    assert assessment(c.root, "sample", "dsh-standard")["status"] == "accepted"
    detail = decorate(
        c.root,
        {"directory": "sample", "agents": ["dsh-standard", "bkcrab"], "mode": "live"},
    )
    assert detail["accepted_agents"] == ["dsh-standard"]
    f.write_text('{"changed":true}')
    assert assessment(c.root, "sample", "dsh-standard")["status"] == "unreviewed"


def test_symlink_trace_outside_trial_not_read(sample, tmp_path):
    c, key, _, trial = sample
    target = tmp_path / "secret"
    target.write_text('{"request_id":"SECRET"}\n')
    source = trial / "agent/native_wire_requests.jsonl"
    source.unlink()
    source.symlink_to(target)
    result = trace(c, "r", key)
    assert "SECRET" not in json.dumps(result)
    assert result["warnings"]


def test_malformed_shapes_do_not_become_zero_usage(sample):
    c, key, write, _ = sample
    write("wire_responses.jsonl", [{"request_id": ["bad"], "usage": []}])
    result = trace(c, "r", key)
    assert result["metrics"]["input_tokens"] is None
    assert result["warnings"]


def test_new_trial_invalidates_existing_review(sample):
    c, _, _, trial = sample
    f = trial / "result.json"
    (c.root / "console_reviews.json").write_text(
        json.dumps(
            {
                "sample": {
                    "dsh-standard": {
                        "status": "accepted",
                        "reason": "checked",
                        "evidence": {
                            str(f.relative_to(c.root)): hashlib.sha256(
                                f.read_bytes()
                            ).hexdigest()
                        },
                    }
                }
            }
        )
    )
    new = trial.parent / "task__new"
    new.mkdir()
    (new / "result.json").write_text('{"task_name":"new"}')
    assert assessment(c.root, "sample", "dsh-standard")["status"] == "unreviewed"


def test_tool_ids_override_positional_association(sample):
    c, key, write, _ = sample
    # More relay attempts than native rounds: positional joining is impossible.
    write(
        "native_wire_requests.jsonl",
        [{"request_id": "extra", "body": {}}, {"request_id": "one", "body": {}}],
    )
    write(
        "wire_chunks.jsonl",
        [
            {
                "request_id": "one",
                "event": {"choices": [{"delta": {"tool_calls": [{"id": "t"}]}}]},
            }
        ],
    )
    result = trace(c, "r", key)
    assert result["rounds"][0]["request_id"] == "one"
    assert result["rounds"][0]["association"] == "tool_call_id"
    assert result["rounds"][0]["request_source"] == "native_wire_requests.jsonl:2"
    assert result["metrics"]["input_tokens"] is None
