import asyncio,json
from pathlib import Path
import pytest
from test_qq_expressions import lab,row
from test_qq_delivery import config,event
from saya_app.services.qq_bridge import OneBotClient,normalize_event
from saya_app.services.qq_presentation import clean_control_output
from saya_app.services.qq_voice import spoken_body


@pytest.mark.parametrize('tag',['[MODAL_SAYA: affectionate]','［modal_saya： affectionate］','[MODEL_SAYA: affectionate]'])
def test_mood_namespace_drift_hidden_in_prose_and_speech_but_not_literal_code(tag):
    assert clean_control_output(tag+'我在。')=='我在。'
    assert spoken_body(tag+'我在。')=='我在。'
    assert clean_control_output('`'+tag+'`')=='`'+tag+'`'
    assert clean_control_output('[MODEL_SAYA: deepseek] [普通说明]')=='[MODEL_SAYA: deepseek] [普通说明]'


def test_retired_face_policy_stays_off_without_rewriting_original_choice(tmp_path):
    async def run():
        b,c=await lab(tmp_path)
        try:
            with pytest.raises(ValueError):b.expressions.configure('20000',1,'face',confirmed=True)
            with b.store.connect() as db:db.execute("update qq_expression_policy set mode='face'")
            state=b.expressions.state()
            assert state['mode']=='off' and state['retired_mode']=='face' and state['expression_revision']==1
            with b.store.connect() as db:assert db.execute('select mode from qq_expression_policy').fetchone()[0]=='face'
            b.submit(event('你好',32101));await b.queue.join()
            assert c.sent and not c.reactions and not c.voices
            assert 'face' not in b.expressions.command(normalize_event(event('/expression'),b.settings)[0])
        finally:await b.stop()
    asyncio.run(run())


@pytest.mark.parametrize('method',['send_inline','send_expression'])
def test_native_face_wire_send_rejected_before_transport(method):
    async def run():
        c=OneBotClient(config());calls=[]
        async def call(*args):calls.append(args);return {'message_id':801}
        c.call=call;m=normalize_event(event(),config())[0]
        try:
            with pytest.raises(ValueError):
                if method=='send_inline':await c.send_inline(m,'你好',kind='face',key='14',offset=0)
                else:await c.send_expression(m,'face','14')
            assert not calls
        finally:await c.close()
    asyncio.run(run())


def test_pending_legacy_face_cannot_resume_after_text_or_replace_it_with_another_visual(tmp_path):
    from saya_app.services import qq_delivery
    async def run():
        b,c=await lab(tmp_path)
        try:
            m=normalize_event(event('q',32102),b.settings)[0];scope=m.scope+(':shared' if m.shared else ':isolated')
            result=qq_delivery.create(b.store,m,scope,'你好。',100)
            plan={'kind':'face','id':'14','inline':True,'anchor':'你好','placement':'after','policy_revision':1}
            b.expressions.bind(result,scope,plan)
            assert b.expressions.inline_part(m,scope,result,'你好。') is None
            await qq_delivery.send_batch(b.store,c,m,scope,result,3,False)
            await b.expressions.after_text(m,scope,result)
            assert len(c.sent)==1 and not c.reactions and not c.voices
            assert row(b)['state']=='skipped' and row(b)['error']=='retired_expression'
        finally:await b.stop()
    asyncio.run(run())


def test_face_mode_is_absent_from_settings_surface_and_runtime_capabilities(tmp_path):
    from saya_app.services.qq_capabilities import snapshot
    async def run():
        b,c=await lab(tmp_path)
        try:
            cap=snapshot(b,normalize_event(event(),b.settings)[0])
            assert 'qq_faces' not in cap['expressions'] and cap['voice_output']['compatible_with']==['kaomoji']
            assert 'value="face"' not in (Path(__file__).parents[1]/'static/qq-favorites.html').read_text(encoding='utf8')
        finally:await b.stop()
    asyncio.run(run())
