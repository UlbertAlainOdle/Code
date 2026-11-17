import asyncio,json
import httpx,pytest
from scripts.eval_qq_recall_r4 import seed
from saya_app.services.memory_recall_reference import inspect
from test_recall_reference import history
from saya_app.services.memory_recall_contract import recall_rules
from saya_app.services.memory_v4.prompt_compiler import _estimate_tokens
from test_recall_reference import history
from test_qq_chat_runtime import server
from r4_web_lab import web_lab


def test_ambiguity_is_a_plan_not_a_latest_guess(tmp_path):
    kb,ids=seed(tmp_path)
    plan=inspect(kb,'回到刚才那个计划，我为什么选它？',history(ids))
    assert plan['status']=='clarify' and set(plan['candidates'])=={'青岚计划','棠木计划'}
    kb.set_memory_use_policy(ids['choice'],True);kb.set_memory_use_policy(ids['reason'],True)
    plan=inspect(kb,'回到刚才那个计划，我为什么选它？',history(ids))
    assert '青岚计划' not in plan['candidates']


def test_motive_rule_fits_existing_budget():
    assert 'One reason does not rule out others' in recall_rules()
    assert _estimate_tokens(recall_rules())<=220
    from saya_app.services.memory_v4.model_adapters import _tool_policy_lines
    assert 'Without a read, never claim no record was found' in '\n'.join(_tool_policy_lines([{'name':'memory.query'}]))


def test_named_return_is_not_forced_to_clarify_without_old_history(tmp_path):
    kb,ids=seed(tmp_path)
    plan=inspect(kb,'回到青岚计划，我之前为何选择离线？',[])
    assert plan['status']=='resolved' and plan['focus']=='青岚计划'
    assert inspect(kb,'回到青岚计划和棠木计划分别说说',history(ids)) is None


def test_agent_reference_seed_is_current_and_cannot_survive_revocation(tmp_path):
    kb,ids=seed(tmp_path)
    plan=inspect(kb,'回到海上作业那个计划，我为什么选择它？',history(ids))
    handler=kb._memory_v4_capability_registry(memory_query_options={
        'topic_seed':plan['focus'],'reference_plan':plan,'question_text':'回到海上作业那个计划，我为什么选择它？'
    }).get('memory.query').handler
    result=handler({'query':'最近'})
    assert {ids['choice'],ids['reason']}<={item['id'] for item in result['items']}
    kb.set_memory_use_policy(ids['reason'],True)
    result=handler({'query':'青岚计划','reference_plan':None})
    assert result['status']=='clarification_required' and result['items']==[]


@pytest.mark.parametrize('agent',[False,True])
def test_ambiguous_return_is_clarified_without_model_or_fact_access(server,tmp_path,agent):
    kb,ids=seed(tmp_path);calls=[]
    def model(request):
        calls.append(json.loads(request.content))
        value={'choices':[{'delta':{'content':'青岚计划的理由是海上作业经常断网。'},'finish_reason':'stop'}]}
        return httpx.Response(200,text='data: '+json.dumps(value,ensure_ascii=False)+'\n\ndata: [DONE]\n\n')
    async def run():
        with web_lab(server,tmp_path/'lab',kb) as lab:
            async with httpx.AsyncClient(transport=httpx.MockTransport(model)) as client:
                await lab.turn(client,'我之前在青岚计划为什么选离线？')
                before=len(calls)
                result=await lab.turn(client,'回到刚才那个计划，我为什么选它？',agent=agent)
                assert len(calls)==before
                assert '请明确名称' in result['answer']
                assert result['audit']['status']=='clarification_required'
                assert result['audit']['selected']==[]
                assert len(result['entries'])==4
    asyncio.run(run())
