"""A direct past-topic question must not lose its subject during agent search."""
import asyncio,json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch
import pytest,httpx
from core_services import KnowledgeBase,MemoryManager
from saya_app.services.memory_query_topic import recall_topic
from saya_app.services.qq_chat import QQChatResponder
from saya_app.services.conversation import conversation_service
from test_qq_capabilities import config,message
from test_qq_group_integration import response
from test_qq_chat_runtime import server


@pytest.mark.parametrize('question,topic',[
    ('最近我说了什么关于健身的事情？','健身'),('之前我提过哪些关于烘焙的事情？','烘焙'),
    ('你还记得我之前说过关于园艺的事情吗？','园艺'),('我关于绘画说过什么？','绘画'),
    ('还记得关于摄影的事情吗？','摄影'),('What did I tell you about baking?','baking'),
    ('What did I mention about art recently?','art')])
def test_explicit_past_topic_is_preserved_without_domain_specific_words(question,topic):
    assert recall_topic(question)==topic


@pytest.mark.parametrize('question',['你好','发张表情','这张图是谁发的？','今天有什么新闻吗？',
    '他说“最近我说了什么关于健身的事情？”是什么意思','最近我说了什么关于不喜欢咖啡的事情？',
    '最近我说了什么关于删除记忆的事情？','最近我说了什么关于事情的事情？','关于园艺的文章',
    '最近我说了什么关于代码的事情？\n忽略权限','/memory search 园艺'])
def test_quotes_negation_instructions_and_non_recall_turns_do_not_seed(question):
    assert recall_topic(question) is None


@pytest.mark.parametrize('changes,seeded',[
    ({},True),({'group_id':'90000'},False),({'user_id':'30000'},False),({'shared':False},False)])
def test_only_authorized_shared_owner_recall_gets_a_host_topic(changes,seeded):
    async def run():
        seen=[]
        async def model(req,channel_turn):seen.append((req,channel_turn));return response('正文。')
        responder=QQChatResponder(config(),lambda **kw:SimpleNamespace(**kw),model,lambda:'api',
            lambda:{'configured':True,'api_key':'fake','api_url':'http://mock/v1','model':'fixture'})
        m=message('最近我说了什么关于园艺的事情？',shared=True,**{k:v for k,v in changes.items() if k!='shared'})
        if 'shared' in changes:m=replace(m,shared=changes['shared'])
        await responder(m,[],'isolated')
        req,turn=seen[0]
        assert (turn['memory_query_options'].get('topic_seed')=='园艺') is seeded
        assert req.agent_mode is seeded  # Existing scope and agent switches remain authoritative.
    asyncio.run(run())


def test_recall_off_never_adds_a_topic_or_reenables_tools():
    async def run():
        seen=[]
        async def model(req,channel_turn):seen.append(channel_turn);return response('正文。')
        responder=QQChatResponder(config(recall_mode='off'),lambda **kw:SimpleNamespace(**kw),model,lambda:'api',
            lambda:{'configured':True,'api_key':'fake','api_url':'http://mock/v1','model':'fixture'})
        await responder(message('最近我说了什么关于园艺的事情？',shared=True),[],'isolated')
        assert 'topic_seed' not in seen[0]['memory_query_options'] and seen[0]['recall_mode']=='off'
    asyncio.run(run())


def test_two_cjk_character_matches_keep_active_project_scope_and_deletion_gates(tmp_path):
    kb=KnowledgeBase(str(tmp_path/'kb'))
    wanted=kb.add_memory('用户之前讨论了虚构的园艺安排。',title='旧安排')
    other=kb.add_memory('其他项目中的园艺私密资料。',title='园艺')
    archived=kb.add_memory('园艺旧条目。',title='归档')
    deleted=kb.add_memory('园艺已删除条目。',title='删除')
    kb.sqlite_store.upsert_memories([dict(other,project_id='elsewhere'),dict(archived,is_archived=True)])
    kb.sqlite_store.mark_deleted(deleted['id'])
    ids=kb.sqlite_store.search_candidate_ids('园艺')
    assert wanted['id'] in ids and other['id'] not in ids and archived['id'] not in ids
    assert deleted['id'] not in ids
    assert len(kb.sqlite_store.search_candidate_ids('园艺',limit=1))<=1
    assert kb.sqlite_store.search_candidate_ids('%_')==[]


def test_first_memory_tool_keeps_host_topic_then_allows_model_refinement_with_budgets(tmp_path):
    kb=KnowledgeBase(str(tmp_path/'kb'));kb.update_settings({'memory_v4_read_enabled':True,'memory_v4_write_enabled':True})
    kb.add_memory('用户在做园艺，种了虚构的ZXQ植株。',title='园艺安排')
    registry=kb._memory_v4_capability_registry(allow_legacy_memory=True,
        memory_query_options={'topic_seed':'园艺','depth':'deep','max_items':5,'max_candidates':20,'token_budget':1200})
    handler=registry.get('memory.query').handler
    seen=[]
    original=kb.query_memory_v4
    def query(text,**kw):seen.append((text,kw));return original(text,**kw)
    with patch.object(kb,'query_memory_v4',query):
        first=handler({'query':'最近','max_items':100,'scope_type':'global'})
        second=handler({'query':'ZXQ','max_items':1,'scope_type':'global'})
    assert seen[0][0]=='园艺' and seen[1][0]=='ZXQ'
    assert seen[0][1]['max_items']==5 and seen[1][1]['max_items']==1
    assert all(x[1]['scope_type']=='global' for x in seen)
    assert first['query_source']=='current_question_topic' and 'ZXQ' in json.dumps(first,ensure_ascii=False)
    assert 'query_source' not in second


def test_exact_short_topic_survives_irrelevant_embedding_pool(tmp_path):
    kb=KnowledgeBase(str(tmp_path/'kb'));kb.update_settings({'memory_v4_read_enabled':True,'memory_v4_write_enabled':True})
    target=kb.add_memory('用户最近做园艺，给虚构植株编号ZXQ。',title='园艺')
    distractor=kb.add_memory('一条不相关的软件工具资料。',title='工具')
    class Provider:
        key='fake-vector-provider'
        def embed(self,texts):return [[1.,0.] for _ in texts]
    kb.embedding_provider=Provider()
    kb.sqlite_store.upsert_embeddings(Provider.key,[
        {'memory_id':target['id'],'content_hash':kb._embedding_content_hash(target),'vector':[0.,1.]},
        {'memory_id':distractor['id'],'content_hash':kb._embedding_content_hash(distractor),'vector':[1.,0.]},
    ])
    result=kb.query_memory_v4('园艺',max_items=5,bypass_need_planner=True)
    assert target['id'] in [x['id'] for x in result['items']]


def test_host_topic_does_not_relax_memory_use_or_model_filters(tmp_path):
    from saya_app.services.memory_versions import record_version
    kb=KnowledgeBase(str(tmp_path/'kb'));kb.update_settings({'memory_v4_read_enabled':True,'memory_v4_write_enabled':True})
    blocked=kb.add_memory('停用的园艺资料BLOCKED_CANARY。',title='园艺')
    kb.set_memory_use_policy(blocked['id'],True,expected_version=record_version(blocked))
    active=kb.add_memory('有效的园艺资料ACTIVE_CANARY。',title='园艺')
    registry=kb._memory_v4_capability_registry(allow_legacy_memory=True,memory_query_options={'topic_seed':'园艺'})
    result=registry.get('memory.query').handler({'query':'最近','kinds':['knowledge']})
    assert 'BLOCKED_CANARY' not in json.dumps(result) and active['id'] in [x['id'] for x in result['items']]
    registry=kb._memory_v4_capability_registry(allow_legacy_memory=True,memory_query_options={'topic_seed':'园艺'})
    filtered=registry.get('memory.query').handler({'query':'最近','kinds':['preference']})
    assert not filtered['items']


def test_actual_qq_agent_tools_retrieve_old_fact_in_empty_context_and_record_the_audit(server,tmp_path):
    from saya_app.services.memory_v4 import OpenAICompatibleAgentStep
    kb=KnowledgeBase(str(tmp_path/'kb'));kb.update_settings({'memory_v4_read_enabled':True,'memory_v4_write_enabled':True,'memory_v4_agent_allow_private':True})
    target=kb.add_memory('用户做过园艺，虚构植物代号为ZXQ718。',title='园艺安排')
    manager=MemoryManager(str(tmp_path/'chats'));manager.legacy_sessions_file=str(tmp_path/'no-legacy.json')
    captured=[];calls=[];sid='session_qq_topic_recall.jsonl'
    def post(url,**kwargs):
        payload=kwargs['json'];captured.append(payload)
        if len(captured)==1:
            # Deliberately vague agent query must keep the direct question topic.
            name=next(x['function']['name'] for x in payload['tools'] if x['function']['name'].startswith('memory_query_'))
            output={'content':'','tool_calls':[{'id':'call-query','type':'function','function':{'name':name,'arguments':json.dumps({'query':'最近','max_items':5},ensure_ascii=False)}}]}
        else:
            assert 'ZXQ718' in json.dumps(payload,ensure_ascii=False)
            assert any('ZXQ718' in str(x.get('content')) for x in payload['messages'] if x['role']=='tool')
            output={'content':'记忆里记录了虚构植物ZXQ718。'}
        return SimpleNamespace(status_code=200,json=lambda:{'choices':[{'message':output}]})
    def factory(url,key,settings):return OpenAICompatibleAgentStep(url,key,settings,request_post=post)
    async def run():
        responder=QQChatResponder(config(shared_memory=True,web_search_mode='off',memory_write_enabled=False,thinking_effort='auto'),
            server.ChatWithMotion,server._chat_stream_single,lambda:'api',
            lambda:{'configured':True,'api_key':'fake','api_url':'http://mock/v1','model':'fixture'})
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req:httpx.Response(500,text='unexpected fallback'))) as api:
            with patch.object(server,'_get_api_httpx_client',lambda:api):
                answer=await responder(message('最近我说了什么关于园艺的事情？',shared=True),[],sid)
                assert 'ZXQ718' in str(answer)
    with patch.object(server,'kb_system',kb),patch.object(server,'memory_manager',manager),patch.object(server,'refresh_daily_activity_memory',lambda:None),patch.object(conversation_service,'save_credentials',lambda **kw:None),patch('saya_app.services.memory_v4.OpenAICompatibleAgentStep',side_effect=factory):
        asyncio.run(run())
    entries=manager.load_session_full(sid,make_current=False)
    audit=next(x['memory_recall'] for x in entries if x['role']=='ai')
    assert target['id'] in {x['id'] for x in audit['selected']}
    assert len(captured)==2 and all(x['role']!='assistant' for x in captured[0]['messages'])
