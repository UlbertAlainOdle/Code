import asyncio,json
from unittest.mock import patch
import pytest
from test_memory_temporal_history import changed,OLD,NEW,QUESTION
from test_qq_chat_runtime import server
from r4_web_lab import web_lab


def registry(kb,question=QUESTION):
    return kb._memory_v4_capability_registry(memory_query_options={'question_text':question})


def test_truncated_summary_policy_requires_available_full_reader():
    from saya_app.services.memory_v4.model_adapters import _tool_policy_lines
    both=[{'name':'memory_query_fixture','native_name':'memory.query'},
          {'name':'memory_read_fixture','native_name':'memory.read'}]
    policy='\n'.join(_tool_policy_lines(both))
    assert 'call memory_read_fixture for the matching IDs before answering' in policy
    assert 'Do not discuss unasked periods' in policy
    assert 'memory_read_fixture' not in '\n'.join(_tool_policy_lines(both[:1]))


def test_query_and_full_read_share_the_original_question_and_revision(tmp_path):
    kb,identifier=changed(tmp_path)
    tools=registry(kb)
    query=tools.get('memory.query').handler({'query':'槐汀晚餐计划','question_text':'现在为什么用烤箱？'})
    read=tools.get('memory.read').handler({'ids':[identifier],'question_text':'现在用烤箱'})
    for result in (query,read):
        assert len(result['items'])==1
        row=result['items'][0]
        assert row['canonical_text']==OLD and row['revision']==1
        assert row['historical_source']=={'revision':1,'current_revision':2}
        assert result['evidence_contract']['reason_passage_ids']==[identifier]
        assert '蒸锅坏了' not in json.dumps(result,ensure_ascii=False)
    assert kb._find_memory(identifier)['content']==NEW


def test_tool_argument_cannot_change_a_current_question_into_a_history_request(tmp_path):
    kb,identifier=changed(tmp_path)
    tools=registry(kb,'槐汀晚餐计划现在用什么，为什么？')
    for name,args in [('memory.query',{'query':'槐汀晚餐计划','question_text':QUESTION}),
                      ('memory.read',{'ids':[identifier],'question_text':QUESTION})]:
        result=tools.get(name).handler(args)
        assert result['items'][0]['canonical_text']==NEW
        assert not result['items'][0].get('historical_source')


@pytest.mark.parametrize('action',['block','forget','correct','reopen'])
def test_cached_registry_cannot_reopen_revoked_or_corrected_history(tmp_path,action):
    kb,identifier=changed(tmp_path);tools=registry(kb)
    assert tools.get('memory.read').handler({'ids':[identifier]})['items'][0]['canonical_text']==OLD
    if action in {'block','reopen'}:
        kb.set_memory_use_policy(identifier,True)
        if action=='reopen':kb.set_memory_use_policy(identifier,False)
    elif action=='forget':kb.process_memory_write_v4('忘掉槐汀晚餐计划',message_id='forget')
    else:kb.update_memory(identifier,{'content':'其实原记录不属实，槐汀晚餐计划从未使用蒸锅。'})
    for name,args in [('memory.query',{'query':'槐汀晚餐计划'}),('memory.read',{'ids':[identifier]})]:
        result=tools.get(name).handler(args)
        assert '炉灶要留给汤锅' not in json.dumps(result,ensure_ascii=False)
        assert not any(row.get('historical_source') for row in result['items'])


def test_long_history_summary_and_full_read_keep_the_same_version_and_truncation(tmp_path):
    kb,identifier=changed(tmp_path)
    # Use a newly written long source and normal transition rather than patching a proof.
    kb.capture_explicit_memory('记住：虚构的棠溪材料计划使用纸套。'+'补充材料。'*65+'因为纸边容易起毛。')
    result=kb.process_memory_write_v4('更正：虚构的棠溪材料计划不再用纸套，改用布袋。',message_id='long-change')
    assert result['committed']
    long_id=result['committed'][0]['memory']['id']
    tools=registry(kb,'棠溪材料计划改用布袋之前，为什么用纸套？')
    short=tools.get('memory.query').handler({'query':'棠溪材料计划'})
    assert short['items'][0]['text_truncated'] and len(short['items'][0]['canonical_text'])==240
    full=tools.get('memory.read').handler({'ids':[long_id]})
    assert full['items'][0]['canonical_text'].endswith('因为纸边容易起毛。')
    assert short['items'][0]['historical_source']==full['items'][0]['historical_source']


def test_default_agent_runs_query_then_read_without_switching_to_current_text(server,tmp_path):
    kb,identifier=changed(tmp_path)
    kb.update_settings({'memory_v4_agent_allow_private':True})
    calls=[]
    def completion(context):
        calls.append(context)
        tool_rows=[json.loads(r['content']) for r in context if r.get('role')=='tool']
        assert tool_rows and all(OLD in json.dumps(r,ensure_ascii=False) for r in tool_rows)
        assert '蒸锅坏了' not in json.dumps(tool_rows,ensure_ascii=False)
        assert any(r.get('role')=='system' and '[Host recall answer scope]' in r.get('content','') for r in context)
        if len(calls)==1:
            return {'tool_calls':[{'id':'r','name':'memory.read','arguments':{'memory_ids':[identifier]}}]}
        return {'final_answer':'当时因为炉灶要留给汤锅，所以使用蒸锅。'}
    async def run():
        from saya_app.services.memory_v4 import OpenAICompatibleAgentStep
        from types import SimpleNamespace
        with web_lab(server,tmp_path/'web',kb) as lab:
            def provider(url,**kwargs):
                payload=kwargs['json']
                value=completion(payload['messages'])
                if 'tool_calls' in value:
                    message={'content':'','tool_calls':[{'id':c['id'],'type':'function','function':{
                        'name':next(t['function']['name'] for t in payload['tools'] if t['function']['name'].startswith(c['name'].replace('.','_')+'_')),
                        'arguments':json.dumps(c['arguments'])}} for c in value['tool_calls']]}
                else:message={'content':value['final_answer']}
                return SimpleNamespace(status_code=200,json=lambda:{'choices':[{'message':message,'finish_reason':'stop'}],
                    'usage':{'prompt_tokens':5,'completion_tokens':2,'total_tokens':7}})
            def factory(url,key,settings):return OpenAICompatibleAgentStep(url,key,settings,request_post=provider)
            import httpx
            with patch('saya_app.services.memory_v4.OpenAICompatibleAgentStep',side_effect=factory):
                async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req:httpx.Response(500))) as client:
                    result=await lab.turn(client,QUESTION,agent=True)
            assert '汤锅' in result['answer'] and len(calls)==2
            assert len(lab.usage)==2 and all(u['stage']=='web_answer' and u['usage']['total_tokens']==7 for u in lab.usage)
            assert result['audit']['historical_sources'][identifier]=={'revision':1,'current_revision':2}
    asyncio.run(run())


@pytest.mark.parametrize('question',[
    '棠溪材料计划为什么应该用纸套？',
    '棠溪材料计划为什么用纸套？请解释一般原理。',
    '棠溪材料计划为什么用纸套？推测可能原因。',
    '他说“棠溪材料计划为什么用纸套？”',
    '棠溪材料计划目前用什么？',
])
def test_host_read_does_not_replace_advice_explanation_quotes_or_state_only(question):
    from saya_app.services.memory_agent_recall import prepare_step
    from saya_app.services.memory_v4 import CapabilityRegistry
    original=lambda context,capabilities:{'final_answer':'model'}
    assert prepare_step(original,CapabilityRegistry(),{'question_text':question}) is original


def host_loop(*,policy=None,cancel=None,on_query=None,long=True):
    from saya_app.services.memory_v4 import Capability,CapabilityRegistry,CapabilityPolicy,AgentLoop
    from saya_app.services.memory_agent_recall import prepare_step
    calls=[];requests=[];tools=CapabilityRegistry()
    def query(args):
        calls.append(('query',args))
        if on_query:on_query()
        return {'items':[{'id':'memory:long','canonical_text':'截断材料','text_truncated':long}]}
    def read(args):
        calls.append(('read',args))
        return {'items':[{'id':'memory:long','canonical_text':'因为需要避光。','text_truncated':False}]}
    for name,handler in [('memory.query',query),('memory.read',read)]:
        tools.register(Capability(name=name,description=name,handler=handler,estimated_cost=.1,estimated_latency_ms=10))
    def provider(context,capabilities):
        requests.append(context)
        return {'final_answer':'有界回答'}
    result=AgentLoop(tools).run('host',prepare_step(provider,tools,{'question_text':'棠溪材料计划为什么用纸套？'}),
        policy=policy or CapabilityPolicy(),cancel_event=cancel)
    return result,calls,requests


def test_host_full_read_precedes_one_provider_request_within_existing_loop():
    result,calls,requests=host_loop()
    assert result.status=='completed' and result.steps==3
    assert [name for name,args in calls]==['query','read'] and len(requests)==1
    assert [row['name'] for row in requests[0] if row['role']=='tool']==['memory.query','memory.read']
    assert '因为需要避光' in json.dumps(requests[0],ensure_ascii=False)


@pytest.mark.parametrize('case',['deny','no_reader','cost','latency','steps','cancel'])
def test_host_reads_obey_existing_policy_budgets_and_cancel(case):
    from threading import Event
    from saya_app.services.memory_v4 import CapabilityPolicy
    policies={'deny':CapabilityPolicy(mode='deny'),'no_reader':CapabilityPolicy(allowed_names={'memory.query'}),
        'cost':CapabilityPolicy(max_total_cost=.1),'latency':CapabilityPolicy(max_total_latency_ms=10),
        'steps':CapabilityPolicy(max_steps=1),'cancel':CapabilityPolicy()}
    cancel=Event()
    result,calls,requests=host_loop(policy=policies[case],cancel=cancel,on_query=cancel.set if case=='cancel' else None)
    assert [name for name,args in calls]==([] if case=='deny' else ['query'])
    if case in {'steps','cancel'}:
        assert not requests
        assert result.reason==('max_steps' if case=='steps' else 'cancelled')
    else:
        assert result.status=='completed' and len(requests)==1
        if case in {'cost','latency'}:
            assert any(row.get('status')=='budget_exhausted' for row in requests[0])



def test_full_history_read_preserves_the_existing_public_text_cap(tmp_path):
    kb,identifier=changed(tmp_path)
    kb.capture_explicit_memory('记住：虚构的蕨川材料计划使用纸套。'+'核对时保留编号。'*600+'因为样本需要避光。')
    result=kb.process_memory_write_v4('更正：虚构的蕨川材料计划不再用纸套，改用布袋。',message_id='cap-change')
    assert result['committed']
    identifier=result['committed'][0]['memory']['id']
    data=registry(kb,'蕨川材料计划改用布袋之前，为什么用纸套？').get('memory.read').handler({'ids':[identifier]})
    row=data['items'][0]
    assert len(row['canonical_text'])==4000 and row['text_truncated']
    assert row['historical_source']=={'revision':1,'current_revision':2}
    assert '样本需要避光' not in json.dumps(data,ensure_ascii=False)
    assert data['evidence_contract']['reason_status']=='not_found_in_selected_material'


@pytest.mark.parametrize('case',['success','http','transport','json','no_usage','ledger_failure'])
def test_agent_usage_counts_actual_provider_attempts_without_retries(case):
    from types import SimpleNamespace
    from saya_app.services.memory_v4 import OpenAICompatibleAgentStep,AgentModelSettings
    from saya_app.services.memory_v4.model_adapters import ModelAdapterError
    attempts=[];records=[]
    def post(*args,**kwargs):
        attempts.append(True)
        if case=='transport':raise TimeoutError('offline')
        def body():
            if case=='json':raise ValueError('bad JSON')
            return {'choices':[{'message':{'content':'answer'}}],
                **({} if case=='no_usage' else {'usage':{'prompt_tokens':10,'completion_tokens':3,'total_tokens':13,
                'completion_tokens_details':{'reasoning_tokens':1,'private':'hidden'},'private_payload':'hidden'}})}
        return SimpleNamespace(status_code=503 if case=='http' else 200,json=body)
    adapter=OpenAICompatibleAgentStep('http://provider/v1','fixture',AgentModelSettings('fixture'),request_post=post)
    def record(usage,completed,elapsed):
        records.append((usage,completed,elapsed))
        if case=='ledger_failure':raise OSError('ledger unavailable')
    adapter.usage_callback=record
    if case in {'http','transport','json'}:
        with pytest.raises((ModelAdapterError,TimeoutError)):adapter([{'role':'user','content':'hello'}],[])
    else:assert adapter([{'role':'user','content':'hello'}],[])['final_answer']=='answer'
    assert len(attempts)==len(records)==1
    usage,completed,elapsed=records[0]
    assert completed==(case not in {'http','transport','json'}) and elapsed>=0
    if case in {'http','transport','json','no_usage'}:assert usage is None
    else:assert usage=={'prompt_tokens':10,'completion_tokens':3,'total_tokens':13,'completion_tokens_details':{'reasoning_tokens':1}}
