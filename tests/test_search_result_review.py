import io
import json
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
from agent_harness_eval.resources import fetch_review, live_model
from test_search_review import answer, config, payload, proxy, reviewer


@pytest.mark.parametrize("endpoint", ["/search/exa", "/search/v1/messages"])
@pytest.mark.parametrize("decision,status", [(True,403),(False,200),(None,503)])
def test_result_gate_before_delivery(tmp_path, monkeypatch, endpoint, decision, status):
    seen=[]
    def decide(*args):
        seen.append(args[3]["page_content"])
        if decision is None:
            raise TimeoutError("private details")
        return {"denied":decision,"usage":{"input_tokens":1,"output_tokens":1}}
    monkeypatch.setattr(fetch_review,"decide_state",decide)
    body = payload() if endpoint.endswith("messages") else {"query":"docs","contents":{"highlights":True}}
    with reviewer(answer(0.1)) as (review_url,_), proxy(tmp_path,monkeypatch,config(review_url)) as (base,forwarded):
        class Response(io.BytesIO):
            status=200
            headers={"Content-Type":"application/json"}
        def upstream(request, timeout):
            forwarded.append(json.loads(request.data))
            result = {"content":[{"type":"text","text":"GOLD_PATCH_FIXTURE"}],
                      "results":[{"highlights":["GOLD_PATCH_FIXTURE"]}],
                      "usage":{"input_tokens":3,"output_tokens":1}}
            return Response(json.dumps(result).encode())
        monkeypatch.setattr(live_model,"urlopen",upstream)
        request=Request(base+endpoint,json.dumps(body).encode())
        if status==200:
            response=urlopen(request)
        else:
            with pytest.raises(HTTPError) as caught: urlopen(request)
            response=caught.value
        assert response.code==status
        text=response.read().decode()
        assert len(forwarded)==1 and "GOLD_PATCH_FIXTURE" in seen[0]
        if status!=200:
            assert 'GOLD_PATCH_FIXTURE' not in text
            assert 'input_tokens' not in text and 'private details' not in text
            assert not (tmp_path/'wire_chunks.jsonl').exists()
            assert not (tmp_path/'search_results.jsonl').exists()
    record=json.loads((tmp_path/'search_result_review.jsonl').read_text())
    assert record['outcome']=={200:'allowed',403:'denied',503:'unavailable'}[status]
    if endpoint.endswith('messages'):
        wire=json.loads((tmp_path/'wire_responses.jsonl').read_text())
        assert wire['usage']['input_tokens']==3


def test_streaming_search_rejected_before_upstream(tmp_path,monkeypatch):
    with proxy(tmp_path,monkeypatch,config()) as (base,forwarded):
        with pytest.raises(HTTPError) as caught:
            urlopen(Request(base+'/search/v1/messages',json.dumps({**payload(),'stream':True}).encode()))
        assert caught.value.code==400 and not forwarded
