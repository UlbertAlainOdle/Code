import asyncio,json
from types import SimpleNamespace
from unittest.mock import patch
import httpx,pytest
from core_services import KnowledgeBase
from test_qq_topic_routing import topics,item,create
from test_qq_chat_runtime import server
from r4_web_lab import web_lab
from saya_app.services.qq_chat import context_turn_reference,QQChatResponder
from test_qq_delivery import event
from test_qq_task_scheduler import until
from fastapi.responses import StreamingResponse
from saya_app.services.qq_tasks import current_guard,bound_guard


@pytest.mark.parametrize('named',[None,'梧湖装箱计划'])
@pytest.mark.parametrize('question,include',[
 ('梧湖装箱计划现在先做什么，为什么？',True),
 ('说回梧湖装箱计划，现在贴码前核对什么？',True),
 ('说回梧湖装箱计划，以前为什么这样做？',False),
 ('说回那个曾经只剩一种标签的计划，现在呢？',False),
 ('说回梧湖装箱计划，长期记忆现在记录什么？',False),
 ('梧湖装箱计划上一版为什么这样做？',False),
 ('梧湖装箱计划在2025年为什么这样做？',False),
 ('梧湖装箱计划的长期记忆现在记录什么？',False),
 ('石港观测计划现在先做什么？',False),
])
def test_focused_current_reference_is_only_one_matching_host_card(question,include,named):
    snapshot={'cards':[{'title':'梧湖装箱计划','facts':[{'quote':'B_SOURCE'}]},
                       {'title':'其他计划','facts':[{'quote':'UNRELATED_SOURCE'}]}]}
    channel={'named_topic_read':named,'event_context':snapshot,'context_link':None}
    text=context_turn_reference(channel,focused_recall=True,question=question)
    assert ('B_SOURCE' in text)==include and 'UNRELATED_SOURCE' not in text
    assert len(snapshot['cards'])==2


@pytest.mark.parametrize('agent',[False,True])
@pytest.mark.parametrize('question',['梧湖装箱计划现在先做什么，为什么？','说回梧湖装箱计划，现在先做什么，为什么？'])
@pytest.mark.parametrize('noisy',[False,True])
def test_actual_QQ_generator_keeps_other_current_event_after_stop(server,tmp_path,agent,question,noisy):
    async def run():
        async with topics(tmp_path/'bridge') as (b,s,now,msg,choice,prompts):
            first='石港观测计划由我校对木牌，因为编号有重号。'
            second='梧湖装箱计划由我先核对旧箱清单，因为新增包装还没送到。'
            ids=[]
            for title,text in [('石港观测计划',first),('梧湖装箱计划',second)]:
                source=msg(text);entry=item(text,personal=create(),mode='commitment');entry['title']=title
                choice[0]={'items':[entry]};await s.observe(source,[])
                ids.append(next(c['id'] for c in s.people._canonical('20000') if c['title']==title))
            s.command(msg('/events stop '+ids[0]))
            assert ids[1] in {c['id'] for c in s.cards(msg('/events self')) if s.people.controls.recallable(c,'private')}
            current='梧湖装箱计划的新增包装已经送到。我先验包装封边，确认没有开裂后再给货箱贴码。'
            if noisy:
                for text,mode in [(current,'progress'),*[(question,'question') for _ in range(5)]]:
                    row=item(text,personal={'action':'update','event_id':ids[1],'supersedes':[]},mode=mode);row['title']='梧湖装箱计划'
                    choice[0]={'items':[row]};await s.observe(msg(text),[])
            b.settings=b.settings.model_copy(update={'agent_mode':agent,'recall_mode':'on','web_search_mode':'off'})
            kb=KnowledgeBase(str(tmp_path/'kb'))
            kb.update_settings({'memory_v4_read_enabled':True,'memory_v4_write_enabled':True,'memory_v4_agent_allow_private':True,
                                'memory_embedding_enabled':False,'memory_core_limit':0})
            calls=[];references=[]
            original_reference=server.context_turn_reference
            def reference(channel,**kwargs):
                text=original_reference(channel,**kwargs)
                references.append({'kwargs':kwargs,'named':channel.get('named_topic_read'),'cards':(channel.get('event_context') or {}).get('cards'), 'reference':text})
                return text
            answer='先验包装封边，确认没有开裂后才贴码。' if noisy else '先核对旧箱清单，因为新增包装还没送到。'
            def check(payload):
                calls.append(payload)

            def post(url,**kwargs):
                check(kwargs['json'])
                return SimpleNamespace(status_code=200,json=lambda:{'choices':[{'message':{'content':answer}}],
                    'usage':{'prompt_tokens':10,'completion_tokens':3,'total_tokens':13}})
            def handle(request):
                payload=json.loads(request.content);check(payload)
                if not payload.get('stream'):
                    return httpx.Response(200,json={'choices':[{'message':{'content':'{}'}}]})
                content='data: '+json.dumps({'choices':[{'delta':{'content':answer}}]},ensure_ascii=False)+'\n\ndata: [DONE]\n\n'
                return httpx.Response(200,text=content,headers={'content-type':'text/event-stream'})
            from saya_app.services.memory_v4 import OpenAICompatibleAgentStep
            def factory(url,key,settings):return OpenAICompatibleAgentStep(url,key,settings,request_post=post)
            with web_lab(server,tmp_path/'web',kb) as lab:
                async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
                    with patch.object(server,'context_turn_reference',side_effect=reference),patch.object(server,'_get_api_httpx_client',lambda:client),patch('saya_app.services.memory_v4.OpenAICompatibleAgentStep',side_effect=factory):
                        responder=QQChatResponder(b.settings,server.ChatWithMotion,server._chat_stream_single,lambda:'api',lambda:lab.credentials)
                        responder.episodes=s;responder.context_salience=b.context_salience
                        result=await responder(msg(question),[],'session_qq_current_event.jsonl')
                final=[p for p in calls if agent or p.get('stream')]
                assert ('先验包装封边' if noisy else '新增包装还没送到') in str(result) and len(final)==1
                payload=json.dumps(final[0]['messages'],ensure_ascii=False)
                (tmp_path/'answer-provider.json').write_text(payload,encoding='utf8')
                (tmp_path/'references.json').write_text(json.dumps(references,ensure_ascii=False),encoding='utf8')
                assert (current if noisy else second) in payload and first not in payload
    asyncio.run(run())


def test_current_personal_selection_keeps_latest_direct_progress_before_question_noise():
    from saya_app.services.qq_event_retrieval import evidence_order
    facts=[{'speaker':'20000','quote':'梧湖装箱计划先核对旧箱清单，因为包装没到。','mode':'commitment','at':1},
           {'speaker':'20000','quote':'包装已到，先验封边没有开裂再贴码。','mode':'progress','at':2},
           {'speaker':'20000','quote':'说回梧湖装箱计划，现在贴码前应核对什么？','mode':'question','at':3}]
    result=evidence_order(facts,'说回梧湖装箱计划，现在贴码前应核对什么？')
    assert result[:2]==[facts[1],facts[0]] and len(result)==len(facts)
    # A group contribution's newer timestamp does not override the separate
    # coordination/decision contract through this personal-card selection.
    group=[{**f,'actor':f['speaker']} for f in facts]
    for f in group:f.pop('speaker')
    assert evidence_order(group,'梧湖装箱计划现在贴码前应核对什么？')[0]['mode']=='question'


@pytest.mark.parametrize('change',['unrelated_expiry','selected_stop','selected_revision'])
def test_real_bridge_current_lookup_guards_used_card_not_omitted_history(server,tmp_path,change):
    async def run():
        async with topics(tmp_path/'bridge') as (b,s,now,msg,choice,prompts):
            text='梧湖装箱计划由我先核对旧箱清单，因为新增包装还没送到。'
            source=msg(text);entry=item(text,personal=create(),mode='commitment');entry['title']='梧湖装箱计划'
            choice[0]={'items':[entry]};await s.observe(source,[])
            card=s.people._canonical(source.user_id)[0]
            # A blocked, unrelated history entry leaves the retention window
            # during generation. It contributes no source to this lookup.
            with s.store.connect() as db:
                s.people._guard_source(db,source.user_id,'unrelated',{'visibility':'private','digest':'a'*64,
                    'at':now[0]-b.settings.retention_days*86400+5,'expires':now[0]+5},blocked=True)
            projection=s.people.history_projection(source)
            calls=[]
            async def generate(req,channel_turn=None):
                calls.append(channel_turn)
                assert current_guard() is not None
                assert not channel_turn['history'] and not channel_turn.get('context_link')
                assert channel_turn['context_selector'](['Doctor: OMITTED'],[] )==([],[])
                assert 'history_guard' not in channel_turn['qq_episode']
                assert channel_turn['qq_episode']['refs']
                async def stream():
                    now[0]+=6
                    assert not s.people.valid_projection(source,projection)
                    with bound_guard(None):
                        if change=='selected_stop':s.command(msg('/events stop '+card['id']))
                        elif change=='selected_revision':
                            with s.store.connect() as db:
                                db.execute('update qq_episode_cards set revision=revision+1 where id=?',(card['id'],))
                    yield 'event: answer_complete\ndata: {"text":"CURRENT_CARD_ANSWER"}\n\n'
                return StreamingResponse(stream())
            responder=QQChatResponder(b.settings,server.ChatWithMotion,generate,lambda:'api',
                lambda:{'configured':True,'api_key':'fixture','api_url':'https://fixture.invalid','model':'fixture'})
            responder.episodes=s;responder.context_salience=b.context_salience
            b.bind_responder(responder);choice[0]={'items':[]}
            assert b.submit(event('梧湖装箱计划现在先做什么，为什么？',99001,20000))[0]=='queued'
            await until(lambda:bool(calls));await until(lambda:not b.task_registry.list_active())
            assert len(calls)==1
            with s.store.connect() as db:
                inbox=db.execute('select state from inbox where event_key like ?',('%:99001',)).fetchone()[0]
            if change=='unrelated_expiry':
                assert inbox=='sent' and any('CURRENT_CARD_ANSWER' in row[1] for row in b.client.sent)
            else:
                assert inbox=='cancelled' and not any('CURRENT_CARD_ANSWER' in row[1] for row in b.client.sent)
    asyncio.run(run())


def test_real_bridge_settles_already_expired_card_before_history_guard(server,tmp_path):
    async def run():
        async with topics(tmp_path/'bridge') as (b,s,now,msg,choice,prompts):
            source=msg('旧港虚构计划由我核对编号。')
            row=item(source.text,personal=create(),mode='commitment');row['title']='旧港虚构计划'
            choice[0]={'items':[row]};await s.observe(source,[])
            card=s.people._canonical(source.user_id)[0]
            with s.store.connect() as db:
                data=json.loads(db.execute('select data from qq_episode_cards where id=?',(card['id'],)).fetchone()[0])
                data['expires']=now[0]-1
                for f in data['facts']:f['expires']=now[0]-1
                db.execute('update qq_episode_cards set data=? where id=?',(json.dumps(data),card['id']))
            s.last_sweep=now[0]  # Original 30-second throttle is still in force.
            calls=[]
            async def generate(req,channel_turn=None):
                calls.append(channel_turn)
                assert not s.people._canonical(source.user_id)
                version=s.people.controls.version(source.user_id)
                async def stream():
                    now[0]+=31;s.sweep()
                    assert s.people.controls.version(source.user_id)==version
                    yield 'event: answer_complete\ndata: {"text":"NEW_DISCUSSION_ANSWER"}\n\n'
                return StreamingResponse(stream())
            responder=QQChatResponder(b.settings,server.ChatWithMotion,generate,lambda:'api',
                lambda:{'configured':True,'api_key':'fixture','api_url':'https://fixture.invalid','model':'fixture'})
            responder.episodes=s;responder.context_salience=b.context_salience
            b.bind_responder(responder);choice[0]={'items':[]}
            assert b.submit(event('换个虚构讨论：回声比直达声晚到达时会怎样？',99002,20000))[0]=='queued'
            await until(lambda:bool(calls));await until(lambda:not b.task_registry.list_active())
            assert any('NEW_DISCUSSION_ANSWER' in r[1] for r in b.client.sent)
    asyncio.run(run())


def test_named_self_contained_control_dialogue_omits_unasked_old_topic(server,tmp_path):
    async def run():
        async with topics(tmp_path/'bridge') as (b,s,now,msg,choice,prompts):
            source=msg('梧湖装箱计划由我先验封边，确认没有开裂再贴码。')
            row=item(source.text,personal=create(),mode='commitment');row['title']='梧湖装箱计划'
            choice[0]={'items':[row]};await s.observe(source,[])
            card=s.people._canonical(source.user_id)[0]
            question='请把梧湖装箱计划标记为完成，顺便只用一句话谈谈完成虚构任务后的空落感。'
            message=msg(question);receipt=s.people.controls.handle(message)
            assert '已标记事件完成' in str(receipt) and message.event_control_dialogue
            # The bridge binds this authoritative host outcome before invoking
            # its responder; direct adapter fixtures must carry the same field.
            message.event_control_receipt=getattr(receipt,'payload',str(receipt))
            seen=[]
            async def generate(req,channel_turn=None):
                seen.append(channel_turn)
                assert channel_turn['named_topic_statement']=='梧湖装箱计划'
                assert not channel_turn['history'] and not channel_turn.get('context_link')
                assert channel_turn['context_selector'](['Doctor: UNASKED_OLD_TOPIC'],[])==([],[])
                assert '已标记事件完成' in channel_turn['event_control_receipt']
                assert 'Older unanswered questions' in context_turn_reference(channel_turn)
                async def stream():yield 'event: answer_complete\ndata: {"text":"完成虚构任务后，空落感也可以只是暂时停下来的余韵。"}\n\n'
                return StreamingResponse(stream())
            responder=QQChatResponder(b.settings,server.ChatWithMotion,generate,lambda:'api',
                lambda:{'configured':True,'api_key':'fixture','api_url':'https://fixture.invalid','model':'fixture'})
            responder.episodes=s;responder.context_salience=b.context_salience
            await responder(message,['Doctor: UNASKED_OLD_TOPIC'], 'session_qq_mixed_current.jsonl')
            assert len(seen)==1 and s.people._canonical(source.user_id)[0]['lifecycle']=='completed'
    asyncio.run(run())
