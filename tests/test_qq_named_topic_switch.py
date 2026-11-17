import asyncio
from dataclasses import replace
from fastapi.responses import StreamingResponse
from test_qq_topic_routing import topics,item,create
from test_qq_chat_runtime import server
from saya_app.services.qq_chat import QQChatResponder,context_turn_reference


def test_explicit_new_named_topic_excludes_old_group_question_and_false_rewrite(server,tmp_path):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            s.unified.lifecycle.configure('20000',0,{'enabled':True})
            old=msg('本群星桥读书会：我只在周日下午有空。',user=30000,group=90000)
            choice[0]={'items':[item(old.text,group=create(),mode='commitment')]};await s.observe(old,[])
            oldcard=s.unified.cards(old)[0];before=oldcard['revision']
            current=msg('雾桥阅读讨论：本群先聊聊纸质书和电子书的阅读体验，我觉得两者各有优点。',group=90000)
            choice[0]={'items':[item(current.text,group=create(),mode='proposal')]};await s.observe(current,[])
            assert current.topic_write_focus=='雾桥阅读讨论'
            view=s.unified.context(current)
            assert len(view['refs'])==1 and '星桥' not in view['text'] and '周日' not in view['text']
            assert not s.topics.named_focus(current) # ordinary writes must not trigger summary calls
            history=['Doctor: 星桥读书会，时间是否已定案？',
                'Doctor: 请替另一位把周日下午改为周六下午。',
                'Saya: 旧问题和转述不能代表另一位本人改写。']
            calls=[];count=len(prompts)
            async def generate(req,channel_turn):
                calls.append(channel_turn)
                assert req.user_input==current.text
                assert channel_turn['named_topic_statement']=='雾桥阅读讨论'
                assert channel_turn['history']==[] and channel_turn['context_selector'](history,[])==([],[])
                reference=context_turn_reference(channel_turn)
                assert '星桥读书会' not in reference and '周六' not in reference and '周日' not in reference
                assert '纸质书' in reference
                async def stream():yield 'event: answer_complete\ndata: {"text":"纸质书的翻页手感和电子书的便携性确实各有优点。"}\n\n'
                return StreamingResponse(stream())
            responder=QQChatResponder(b.settings,server.ChatWithMotion,generate,lambda:'api',lambda:{'configured':True,'api_key':'fixture','api_url':'https://fixture.invalid','model':'fixture'})
            responder.episodes=s;responder.context_salience=b.context_salience
            await responder(current,history,'new-subject-fixture')
            assert len(calls)==1 and len(prompts)==count
            assert next(c for c in s.unified.cards(old) if c['id']==oldcard['id'])['revision']==before
    asyncio.run(run())


def test_multiple_topics_or_incomplete_plan_do_not_force_single_subject(tmp_path):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            m=msg('雾桥阅读讨论：纸质书手感很好；云帆音乐讨论：我喜欢轻音乐。',group=90000)
            choice[0]={'items':[item('纸质书手感很好',group=create()),item('我喜欢轻音乐。',group=create())]}
            await s.observe(m,[])
            assert not getattr(m,'topic_write_focus','')
            partial=msg('雾桥阅读讨论：我喜欢纸质书。',group=90000)
            choice[0]={'items':[item(partial.text,group=create())],'unprocessed':True};await s.observe(partial,[])
            assert not getattr(partial,'topic_write_focus','')
            trailing=msg('雾桥阅读讨论：纸质书手感很好。之前那个活动，现在几点？',group=90000)
            choice[0]={'items':[item('纸质书手感很好',group=create())]};await s.observe(trailing,[])
            assert not getattr(trailing,'topic_write_focus','')
    asyncio.run(run())
