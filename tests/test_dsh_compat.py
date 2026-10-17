import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from agent_harness_eval.dsh_compat import SEARCH_MODULE, patched_search_module


def test_unknown_upstream_search_module_fails_closed():
    with pytest.raises(ValueError, match="module changed"):
        patched_search_module(b"unexpected upstream upgrade")


def test_pinned_search_provider_preserves_body_and_citations(tmp_path):
    """Exercise the real pinned provider and model-facing formatter without HTTP."""
    runtime = Path(".cache/dsh-runtime").resolve()
    module = runtime / SEARCH_MODULE
    if not module.exists() or not shutil.which("node"):
        pytest.skip("install locks/dsh-package-lock.json into .cache/dsh-runtime for runtime regression")
    original = module.read_bytes()
    patched, audit = patched_search_module(original)
    assert audit["upstream_sha256"] == hashlib.sha256(original).hexdigest()
    assert audit["patched_sha256"] == hashlib.sha256(patched).hexdigest()
    assert original == module.read_bytes()
    (tmp_path / "node_modules").symlink_to(runtime / "node_modules", target_is_directory=True)
    (tmp_path / "search.mjs").write_bytes(patched)
    (tmp_path / "check.mjs").write_text(r'''
import assert from 'node:assert/strict';
import {DeepSeekSearchProvider} from './search.mjs';
import {formatSearchOutput} from '@deepseek-ai/dsh-tool-web';
const provider = new DeepSeekSearchProvider(() => ({apiKey:'fixture',baseURL:'https://fixture.invalid',model:'fixture',maxTokens:4096,maxUses:5}));
let body;
globalThis.fetch = async () => ({ok:true,json:async () => body});
const source = {type:'web_search_result',url:'https://docs.python.org/3/',title:'Python'};
const search = () => provider.search({query:'documentation'});
body = {content:[
  {type:'thinking',thinking:'do not expose reasoning'},
  {type:'text',text:'First answer paragraph.'},
  {type:'web_search_tool_result',content:[source,source]},
  {type:'text',text:'Second answer paragraph.'},
]};
let result = await search();
assert.equal(result.content,'First answer paragraph.\n\nSecond answer paragraph.');
assert.equal(result.sources.length,1);
assert.equal(result.sources[0].snippet,undefined);
assert.match(formatSearchOutput(result),/Second answer paragraph\./);
assert.doesNotMatch(formatSearchOutput(result),/do not expose reasoning/);
body.content.push({type:'text',text:'Cited answer.',citations:[{url:source.url,cited_text:'Original excerpt'}]});
result = await search();
assert.equal(result.sources[0].snippet,'Original excerpt');
assert.match(result.content,/Cited answer\./);
body = {content:[{type:'web_search_tool_result',content:[source]}]};
assert.equal((await search()).content,'');
body = {content:[{type:'text',text:'No native search results'}]};
await assert.rejects(search(),/no web_search_tool_result/);
console.log(JSON.stringify({passed:true}));
''')
    run = subprocess.run(["node", str(tmp_path / "check.mjs")], capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, run.stderr
    assert json.loads(run.stdout)["passed"]
