import json
import tarfile

import pytest

from agent_harness_eval.agents.frozen_cli import OpenCode, Hermes
from agent_harness_eval.native_cli_bundle import build_bundle


def test_bundle_pins_materialized_bytes(tmp_path):
    binary = tmp_path / 'opencode'
    binary.write_text('#!/bin/sh\necho 1.19.3\n')
    binary.chmod(0o755)
    first = build_bundle('opencode', tmp_path / 'one', binary=binary)
    second = build_bundle('opencode', tmp_path / 'two', binary=binary)
    assert first['bundle_sha256'] == second['bundle_sha256']
    with tarfile.open(tmp_path / 'one/opencode.tar.gz') as archive:
        assert archive.extractfile('opencode').read() == binary.read_bytes()
    assert json.loads((tmp_path / 'one/files.json').read_text())['opencode'] == first['binary_sha256']


def test_transport_config_and_per_trial_state():
    for cls in (OpenCode, Hermes):
        obj = object.__new__(cls)
        obj.model_config = {'name': 'deepseek-flash', 'context_window': 131072, 'max_tokens': 8192}
        text = json.dumps(obj.configuration())
        assert 'http://127.0.0.1:18765/v1' in text
        assert 'deepseek-flash' in text
        assert obj.home_environment()['HOME'].startswith('/tmp/')
        assert obj.home_environment()['XDG_DATA_HOME'].startswith('/logs/agent/')
        # No tool disables or alternate scripted loop is installed by the adapter.
        assert 'disabled' not in text


def test_opencode_event_projection_keeps_tool_ids(tmp_path):
    from agent_harness_eval.native_cli_events import collect_events
    rows = [
        {'type': 'tool_use', 'part': {'tool': 'bash', 'callID': 'c1', 'state': {'input': {'command': 'pwd'}, 'output': '/app'}}},
        {'type': 'text', 'part': {'text': 'done'}},
    ]
    (tmp_path / 'opencode.jsonl').write_text('\n'.join(json.dumps(r) for r in rows))
    events = collect_events(tmp_path, 'opencode')
    assert events[0]['data']['id'] == events[1]['data']['id'] == 'c1'
    assert events[-1]['data']['final_response'] == 'done'


def test_hermes_event_projection_reads_native_db(tmp_path):
    import sqlite3
    from agent_harness_eval.native_cli_events import collect_events
    (tmp_path / 'hermes-home').mkdir()
    path = tmp_path / 'hermes-home/state.db'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE sessions(id TEXT,source TEXT,parent_session_id TEXT)')
        db.execute("INSERT INTO sessions VALUES ('s','oneshot',NULL)")
        db.execute('CREATE TABLE messages(id INTEGER,session_id TEXT,role TEXT,content TEXT,tool_calls TEXT,tool_call_id TEXT,tool_name TEXT,timestamp REAL)')
        db.execute('INSERT INTO messages VALUES (1,\'s\',\'assistant\',\'done\',NULL,NULL,NULL,1)')
    before = path.read_bytes()
    assert collect_events(tmp_path, 'hermes')[-1]['data']['final_response'] == 'done'
    assert path.read_bytes() == before
