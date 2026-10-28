import json
import subprocess
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from agent_harness_eval.resources import fetch_review
from test_search_review import answer, config, proxy, reviewer


@pytest.mark.parametrize("stage", ["content"])
@pytest.mark.parametrize("probability,status", [(0.1, 200), (0.9, 403), (None, 503)])
def test_fetch_review_gate_and_audit(tmp_path, monkeypatch, stage, probability, status):
    body = {"url": "https://example.com/docs", "stage": stage, "content": "page fixture" if stage == "content" else ""}
    with reviewer(answer(probability)) as (endpoint, seen), proxy(tmp_path, monkeypatch, config(endpoint)) as (base, forwarded):
        request = Request(base + "/review/web-fetch", json.dumps(body).encode())
        if status == 200:
            with urlopen(request) as response:
                assert json.load(response)["allowed"] is True
        else:
            with pytest.raises(HTTPError) as caught:
                urlopen(request)
            assert caught.value.code == status
            response = json.loads(caught.value.read())
            assert response["allowed"] is False
            assert response["message"] == (fetch_review.DENIED if status == 403 else fetch_review.UNAVAILABLE)
        assert not forwarded
        assert seen[0][0]["state"]["stage"] == stage
        assert seen[0][0]["state"]["page_content"] == body["content"]
    record = json.loads((tmp_path / "fetch_review.jsonl").read_text())
    assert record["outcome"] == {200:"allowed",403:"denied",503:"unavailable"}[status]
    assert "page fixture" not in json.dumps(record)  # blocked content not exposed in agent-readable audit
    assert not (tmp_path / "wire_responses.jsonl").exists()  # separate reviewer accounting


def test_long_content_checks_tail_and_fails_on_incomplete_review(monkeypatch):
    seen = []
    def decide(*args):
        text = args[3]["page_content"]
        seen.append(text)
        return {"denied": "GOLD_PATCH" in text, "probability": 0.9 if "GOLD_PATCH" in text else 0.1, "usage": {"input_tokens":1,"output_tokens":1}}
    monkeypatch.setattr(fetch_review, "decide_state", decide)
    body = {"stage":"content", "url":"https://example.com/docs", "content":"a"*24000+"GOLD_PATCH"}
    record = {}
    assert fetch_review.review(config(), "key", "task", body, record) is False
    assert len(seen) == 3
    assert "GOLD_PATCH" in seen[-1]
    def unavailable(*args): raise TimeoutError("secret detail")
    monkeypatch.setattr(fetch_review, "decide_state", unavailable)
    with pytest.raises(TimeoutError): fetch_review.review(config(), "key", "task", body, {})
    body["content"] = "x" * 200001
    with pytest.raises(ValueError, match="contract"): fetch_review.review(config(), "key", "task", body, {})


def test_fetch_review_jev_failure_is_unavailable(tmp_path, monkeypatch):
    def fail(*args): raise TimeoutError("secret reviewer endpoint")
    monkeypatch.setattr(fetch_review, "decide_state", fail)
    with proxy(tmp_path, monkeypatch, config()) as (base, _):
        with pytest.raises(HTTPError) as caught:
            urlopen(Request(base+"/review/web-fetch", json.dumps({"stage":"url","url":"https://example.com"}).encode()))
        assert caught.value.code == 503
        assert "secret" not in caught.value.read().decode()
    assert "secret" not in (tmp_path/"fetch_review.jsonl").read_text()


def test_fetch_review_is_enforced_at_platform_boundary(tmp_path, monkeypatch):
    from harbor.agents.installed.base import BaseInstalledAgent
    from agent_harness_eval.agents.native import Native
    class FixtureNative(Native):
        @staticmethod
        def name(): return "fixture"
        async def install(self, environment): pass
        async def run(self, instruction, environment, context): pass
    from agent_harness_eval.provenance import digest
    monkeypatch.setattr(BaseInstalledAgent, "__init__", lambda self, *args, **kwargs: None)
    binary = tmp_path/"binary"
    binary.write_bytes(b"fixture")
    provenance = tmp_path/"provenance.json"
    identity = {"binary_sha256":digest(binary)}
    provenance.write_text(json.dumps(identity))
    agent = FixtureNative(provenance=provenance, model_config={"search_review":config()})
    assert "EVAL_FETCH_REVIEW_URL" not in agent.wrap_model("native")
    provenance.write_text(json.dumps({**identity, "web_fetch_review":True}))
    FixtureNative(provenance=provenance, model_config={"search_review":config()})


def test_dsh_real_fetch_hooks(tmp_path):
    from agent_harness_eval.dsh_compat import patched_fetch_modules
    runtime = Path(".cache/dsh-runtime").resolve()
    if not runtime.exists(): pytest.skip("pinned DSH runtime required")
    files, _ = patched_fetch_modules(runtime)
    native = "node_modules/@deepseek-ai/dsh-web-fetch-http/lib/index.js"
    files[native] = (runtime/native).read_bytes()
    (tmp_path/"node_modules").symlink_to(runtime/"node_modules", target_is_directory=True)
    for name, content in files.items():
        target = Path(name)
        local = target.name if target.name != "index.js" else target.parts[-3]+".mjs"
        if target.parts[-3] == "dsh-web-fetch-http" and target.name == "index.js":
            # Replace only the HTTP transport dependency, preserving real URL,
            # redirect, decoding and review-hook execution.
            content = content.replace(b'const { fetch } = await import("undici");', b'const { fetch } = globalThis;')
            content = content.replace(b'const { Agent, fetch } = await import("undici");',
                                      b'const { Agent } = await import("undici"); const { fetch } = globalThis;')
        (tmp_path/local).write_bytes(content)
    (tmp_path/"test.mjs").write_text(r'''
import assert from 'node:assert/strict';
import {HttpFetchProvider} from './dsh-web-fetch-http.mjs';
import {applyWebFetchTool} from './dsh-tool-web.mjs';
process.env.EVAL_FETCH_REVIEW_URL='http://127.0.0.1:18765/review/web-fetch';
let checks=[], pages=[], deniedStage=null, unavailable=false;
globalThis.fetch=async (url,options)=>{
 if(String(url).includes('/review/web-fetch')){
  const body=JSON.parse(options.body);checks.push(body);
  return new Response(JSON.stringify({allowed:body.stage!==deniedStage}),{status:unavailable?503:body.stage===deniedStage?403:200});
 }
 pages.push(String(url));
 return String(url).endsWith('/start') ? new Response('',{status:302,headers:{location:'/docs'}}) : new Response('PAGE_CONTENT',{headers:{'content-type':'text/plain'}});
};
const p=new HttpFetchProvider({timeoutMs:1000,maxRedirects:5,maxResponseBytes:10000,maxBodyChars:10000,userAgent:'fixture'},async()=>[{address:'93.184.216.34',family:4}]);
let tool;
applyWebFetchTool({systemPrompt:{section(){},getSectionOrder(){return 0}},tools:{register(t){tool=t}},web:{fetch:(r,s)=>p.fetch(r,s)}},30000,50000);
const run=()=>tool.execute({url:'https://example.com/start'},{signal:new AbortController().signal});
let result=await run();
assert.equal(result.body.content,'PAGE_CONTENT');
assert.deepEqual(checks.map(x=>x.stage),['content']);
assert.ok(checks[0].content.includes('PAGE_CONTENT'));
checks=[];pages=[];deniedStage='content';
await assert.rejects(run(),/EVAL_FETCH_DENIED/);assert.equal(pages.length,2);
deniedStage=null;unavailable=true;pages=[];
await assert.rejects(run(),/EVAL_FETCH_REVIEW_UNAVAILABLE/);assert.equal(pages.length,2);
''')
    result = subprocess.run(["node", str(tmp_path/"test.mjs")], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
