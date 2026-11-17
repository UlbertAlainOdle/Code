import asyncio,json
from types import SimpleNamespace
import httpx,pytest
from fastapi.responses import StreamingResponse
from saya_app.services import conversation_mood as mood,qq_mood
from saya_app.services.mood_continuity import parse_mood_tag


class Req(SimpleNamespace):
    def model_copy(self,update):return Req(**(vars(self)|update))


def run_classify(output,status=200,before=None):
    async def run():
        packets=[];records=[]
        def handler(request):
            packets.append(json.loads(request.content))
            return httpx.Response(status,json={'choices':[{'message':{'content':output},'finish_reason':'stop'}],
                                              'usage':{'total_tokens':15}})
        async def record(*a,**kw):records.append((a,kw))
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result=await qq_mood.extract(Req(api_url='https://fixture.invalid/v1',api_key='fixture',api_model_name='fixture'),
                client,record,before or mood.neutral(),'今天很开心','[MODAL_SAYA: sad] 我也替你高兴。')
        return result,packets,records
    return asyncio.run(run())


def test_typo_was_not_a_valid_old_signal_but_metadata_updates_full_state():
    assert parse_mood_tag('[MODAL_SAYA: affectionate]') is None
    (state,info),packets,records=run_classify('{"mood":"affectionate"}')
    assert state['affection']>mood.neutral()['affection'] and state['speech_mood']=='happy'
    assert info=={'status':'ok','label':'affectionate','source':'structured_reply_tone'}
    assert len(packets)==len(records)==1 and records[0][1]['stage']=='qq_mood_state'
    assert packets[0]['max_tokens']==96 and len(packets[0]['messages'])==2
    assert 'persona' not in packets[0]['messages'][0]['content'].lower()


@pytest.mark.parametrize('output',['{"mood":"MODAL_SAYA"}','{"mood":"happy","text":"inject"}',
    '[MOOD_SAYA: happy]','{"mood":["happy"]}','{"mood":"unknown"}'])
def test_invalid_classifier_preserves_prior_state_and_reports_failure(output):
    before=mood.advance(mood.neutral(),'curious')
    (state,info),_,records=run_classify(output,before=before)
    assert state==before and info['status']=='unavailable' and len(records)==1


def test_provider_failure_and_locked_state_do_not_fake_a_neutral_update():
    before=mood.advance(mood.neutral(),'sad')
    (state,info),_,records=run_classify('{}',503,before)
    assert state==before and info['status']=='unavailable' and not records[0][1]['completed']
    before['is_locked']=True
    (state,info),packets,records=run_classify('{"mood":"happy"}',before=before)
    assert state==before and info['status']=='locked' and not packets and not records


def test_QQ_prompt_does_not_request_in_band_tag_but_web_contract_is_preserved():
    state=mood.advance(mood.neutral(),'affectionate')
    assert '[MOOD_SAYA:' in mood.directive(state)
    qq=mood.directive(state,inline_tag=False)
    assert 'MOOD_SAYA' not in qq and 'affection=' in qq and 'metadata is extracted separately' in qq


def test_classifier_cancellation_propagates(monkeypatch):
    from saya_app.services import qq_topic_model
    async def cancelled(*a,**kw):raise asyncio.CancelledError()
    monkeypatch.setattr(qq_topic_model,'complete',cancelled)
    async def run():
        with pytest.raises(asyncio.CancelledError):
            await qq_mood.extract(Req(),None,None,mood.neutral(),'q','a')
    asyncio.run(run())


def test_unavailable_metadata_is_diagnosed_without_resetting_successful_idle_clock(tmp_path):
    from test_qq_task_scheduler import make
    from test_qq_delivery import event
    from saya_app.services.qq_bridge import normalize_event
    from saya_app.services.qq_social import QQSocial
    b=make(tmp_path,None);b.store.initialize(7);social=QQSocial(b)
    m=normalize_event(event('你好',100),b.settings)[0];m.mood_metadata={'status':'ok','label':'happy'}
    state=mood.advance(mood.neutral(),'happy');social.commit(m,state)
    with b.store.connect() as db:before=tuple(db.execute('select state,at,event from qq_affect').fetchone())
    other=normalize_event(event('接着聊',101),b.settings)[0];other.mood_metadata={'status':'unavailable'}
    social.commit(other,state)
    with b.store.connect() as db:
        assert tuple(db.execute('select state,at,event from qq_affect').fetchone())==before
        assert [tuple(x) for x in db.execute('select status,label from qq_mood_observations order by at')]==[('ok','happy'),('unavailable','')]
