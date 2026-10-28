import copy
import json
from concurrent.futures import ThreadPoolExecutor
from urllib.request import Request, urlopen

import pytest
from agent_harness_eval.resources import fetch_review, tool_result_review
from test_search_review import config, proxy


def body(protocol, texts=('ANSWER',), name='web_fetch'):
    if protocol == 'chat':
        return {'model':'main','messages':[
            {'role':'assistant','tool_calls':[{'id':str(i),'type':'function','function':{'name':name,'arguments':json.dumps({'url':'https://example.com/docs'})}} for i in range(len(texts))]},
            *[{'role':'tool','tool_call_id':str(i),'content':t} for i,t in enumerate(texts)]]}
    return {'model':'main','messages':[
        {'role':'assistant','content':[{'id':str(i),'type':'tool_use','name':name,'input':{'url':'https://example.com/docs'}} for i in range(len(texts))]},
        {'role':'user','content':[{'type':'tool_result','tool_use_id':str(i),'content':t} for i,t in enumerate(texts)]}]}


@pytest.fixture
def judge(monkeypatch):
    seen=[]
    def review(c,k,t,b,r):
        seen.append((t,b['content']))
        if b['content']=='FAIL': raise TimeoutError('secret')
        if not isinstance(b['content'],str): raise ValueError()
        return 'ANSWER' not in b['content']
    monkeypatch.setattr(fetch_review,'review',review)
    return seen


def apply(gate,value,records,task='task',cfg=None):
    return gate.filter(value,cfg or config(),'key',task,'request',records.append)


@pytest.mark.parametrize('protocol',['chat','messages'])
def test_parallel_results_replay_and_changed_content(protocol,judge):
    gate=tool_result_review.Gate();records=[]
    value=body(protocol,('ANSWER','documentation'))
    original=copy.deepcopy(value)
    first=apply(gate,value,records)
    assert value==original and 'ANSWER' not in json.dumps(first)
    assert 'documentation' in json.dumps(first) and len(judge)==2
    assert apply(gate,value,records)==first and len(judge)==2
    assert records[-1]['cache_hit']
    apply(gate,body(protocol,('ANSWER changed',)),records)
    assert len(judge)==3
    apply(gate,value,records,task='another task'); assert len(judge)==5
    changed=config();changed['deny_threshold']=0.9
    apply(gate,value,records,cfg=changed); assert len(judge)==7
    assert 'ANSWER' not in json.dumps(records)


@pytest.mark.parametrize('protocol',['chat','messages'])
def test_unavailable_retries_and_unrelated_tools_unchanged(protocol,judge):
    gate=tool_result_review.Gate();records=[];value=body(protocol,('FAIL',))
    assert fetch_review.UNAVAILABLE in json.dumps(apply(gate,value,records),ensure_ascii=False)
    apply(gate,value,records);assert len(judge)==2
    other=body(protocol,('ANSWER',),name='exec')
    assert apply(gate,other,records)==other and len(judge)==2
    disabled=config();disabled['web_fetch']=False
    assert apply(gate,value,records,cfg=disabled)==value


@pytest.mark.parametrize('protocol',['chat','messages'])
def test_unknown_identity_and_unsupported_content_not_exposed(protocol,judge):
    gate=tool_result_review.Gate();records=[];value=body(protocol)
    value['messages'].pop(0)
    assert 'ANSWER' not in json.dumps(apply(gate,value,records)) and not judge
    value=body(protocol,([{'type':'image','data':'ANSWER'}],))
    assert 'ANSWER' not in json.dumps(apply(gate,value,records)) and not judge
    value=body(protocol,([{'type':'text','text':'ANSWER'}],))
    assert 'ANSWER' not in json.dumps(apply(gate,value,records)) and len(judge)==1


def test_conflicting_call_ids_fail_closed(judge):
    value=body('chat');value['messages'].insert(1,body('chat',name='exec')['messages'][0])
    records=[]
    assert 'ANSWER' not in json.dumps(apply(tool_result_review.Gate(),value,records))
    assert not judge


def test_cache_concurrency_and_eviction(judge):
    gate=tool_result_review.Gate(limit=1);records=[];value=body('chat')
    with ThreadPoolExecutor(max_workers=4) as executor:
        results=list(executor.map(lambda _:apply(gate,value,records),range(4)))
    assert len(judge)==1 and all('ANSWER' not in json.dumps(r) for r in results)
    apply(gate,body('chat',('docs',)),records)
    apply(gate,value,records)
    assert len(judge)==3


@pytest.mark.parametrize('name',['web_fetch','webfetch','web_extract'])
def test_four_agent_fetch_names_cannot_bypass(name,judge):
    records=[]
    result=apply(tool_result_review.Gate(),body('chat',name=name),records)
    assert 'ANSWER' not in json.dumps(result)
    assert len(judge)==1 and records[0]['tool_name']==name


@pytest.mark.parametrize('name',['web_search','websearch'])
def test_native_search_intent_before_content(name,judge,monkeypatch):
    from agent_harness_eval.resources import search_review
    monkeypatch.setattr(search_review,'decide',lambda *args:{'denied':True,'usage':{'input_tokens':1,'output_tokens':1}})
    value=body('chat',name=name)
    value['messages'][0]['tool_calls'][0]['function']['arguments']=json.dumps({'query':'exact gold patch'})
    records=[]
    result=apply(tool_result_review.Gate(),value,records)
    assert 'ANSWER' not in json.dumps(result) and not judge
    assert records[0]['intent_check']['denied']


@pytest.mark.parametrize('protocol,path',[('chat','/v1/chat/completions'),('messages','/v1/messages')])
def test_actual_relay_filters_before_upstream_and_logs(tmp_path,monkeypatch,judge,protocol,path):
    value=body(protocol,('ANSWER','docs'))
    with proxy(tmp_path,monkeypatch,config()) as (base,forwarded):
        for _ in range(2):
            with urlopen(Request(base+path,json.dumps(value).encode())) as response:
                assert response.status==200
        assert len(forwarded)==2 and len(judge)==2
        assert 'ANSWER' not in json.dumps(forwarded)
        assert fetch_review.DENIED in json.dumps(forwarded,ensure_ascii=False)
    for name in ['native_wire_requests.jsonl','wire_requests.jsonl','effective_wire_requests.jsonl','fetch_review.jsonl']:
        assert 'ANSWER' not in (tmp_path/name).read_text()


def test_legacy_agent_no_longer_requires_hook_marker(tmp_path,monkeypatch):
    from harbor.agents.installed.base import BaseInstalledAgent
    from agent_harness_eval.agents.native import Native
    class FixtureNative(Native):
        @staticmethod
        def name(): return "fixture"
        async def install(self, environment): pass
        async def run(self, instruction, environment, context): pass
    from agent_harness_eval.provenance import digest
    monkeypatch.setattr(BaseInstalledAgent,'__init__',lambda self,*a,**k:None)
    binary=tmp_path/'binary';binary.write_bytes(b'fixture')
    provenance=tmp_path/'provenance.json';provenance.write_text(json.dumps({'binary_sha256':digest(binary)}))
    agent=FixtureNative(provenance=provenance,model_config={'search_review':config()})
    assert 'EVAL_FETCH_REVIEW_URL' not in agent.wrap_model('run-native-agent')


@pytest.mark.parametrize('protocol,path',[('chat','/v1/chat/completions'),('messages','/v1/messages')])
@pytest.mark.parametrize('probability,expected',[(0.1,'allowed'),(0.9,'denied'),(None,'unavailable')])
def test_real_review_contract_through_model_relay(tmp_path,monkeypatch,protocol,path,probability,expected):
    from test_search_review import reviewer,answer
    with reviewer(answer(probability)) as (endpoint,seen), proxy(tmp_path,monkeypatch,config(endpoint)) as (base,forwarded):
        with urlopen(Request(base+path,json.dumps(body(protocol)).encode())) as response:
            assert response.status==200  # policy feedback is a tool result, not an LLM transport failure
        assert len(seen)==1 and seen[0][0]['state']['page_content']=='ANSWER'
        assert ('ANSWER' in json.dumps(forwarded)) == (expected=='allowed')
    record=json.loads((tmp_path/'fetch_review.jsonl').read_text())
    assert record['outcome']==expected


def test_invalid_search_arguments_fail_closed(monkeypatch, judge):
    value = body('chat', name='web_search')
    value['messages'][0]['tool_calls'][0]['function']['arguments'] = '{broken'
    records = []
    result = apply(tool_result_review.Gate(), value, records)
    assert records[0]['outcome'] == 'unavailable'
    assert 'ANSWER' not in json.dumps(result)
    assert not judge
