import json

from agent_harness_eval.traces import summarize_usage
from agent_harness_eval.usage_view import trial_usage


def test_partial_usage_retains_known_and_separates_auxiliary():
    requests = [{"request_id": i} for i in ("ok", "missing", "no-usage")]
    requests.append({"request_id": "title", "purpose": "session_title"})
    responses = [
        {"request_id": "ok", "usage": {"input_tokens": 100, "output_tokens": 20}},
        {"request_id": "no-usage", "status": 502, "usage": None},
        {"request_id": "title", "usage": {"input_tokens": 900, "output_tokens": 90}},
    ]
    total, evidence = summarize_usage(requests, responses)
    main = evidence["usage_by_purpose"]["agent"]
    assert total == {} and not main["usage_complete"]
    assert main["known_usage"] == {"input_tokens": 100, "output_tokens": 20}
    assert main["missing_usage_requests"] == 2
    assert main["usage_issues"]["缺少响应"] == 1
    assert main["usage_issues"]["响应缺少有效 usage"] == 1


def test_ambiguous_duplicates_and_orphans_are_not_counted():
    requests = [{"request_id": i} for i in ("ok", "duplicate")]
    responses = [{"request_id": i, "usage": {"input_tokens": 5, "output_tokens": 1}}
                 for i in ("ok", "duplicate", "duplicate", "orphan")]
    total, evidence = summarize_usage(requests, responses)
    assert total == {}
    assert evidence["usage_by_purpose"]["agent"]["known_usage"]["input_tokens"] == 5


def test_historical_usage_read_only_and_cache_refresh(tmp_path):
    logs = tmp_path / "agent"
    logs.mkdir()
    requests = logs / "native_wire_requests.jsonl"
    responses = logs / "wire_responses.jsonl"
    requests.write_text('{"request_id":"a"}\n{"request_id":"b"}\n')
    one = json.dumps({"request_id": "a", "usage": {"input_tokens": 50, "output_tokens": 7}}) + "\n"
    responses.write_text(one)
    before = requests.read_bytes(), responses.read_bytes()
    result = trial_usage(tmp_path, {})
    assert result["known_usage"]["input_tokens"] == 50
    assert result["missing_usage_requests"] == 1
    assert before == (requests.read_bytes(), responses.read_bytes())
    responses.write_text(one + one.replace('"a"', '"b"'))
    assert trial_usage(tmp_path, {})["known_usage"]["input_tokens"] == 100


def test_missing_logs_are_unknown_not_zero(tmp_path):
    result = trial_usage(tmp_path, {})
    assert result["known_usage"] == {}
    assert result["missing_usage_requests"] is None


def test_malformed_row_preserves_valid_subtotal(tmp_path):
    logs = tmp_path / "agent"
    logs.mkdir()
    (logs / "native_wire_requests.jsonl").write_text('{"request_id":"a"}\n')
    (logs / "wire_responses.jsonl").write_text(
        '{"request_id":"a","usage":{"input_tokens":50,"output_tokens":7}}\n{bad\n')
    result = trial_usage(tmp_path, {})
    assert not result["usage_complete"]
    assert result["known_usage"]["input_tokens"] == 50
