"""Speech text must agree with an explicitly labelled canonical speech section."""
import asyncio,json,time,sqlite3
from unittest.mock import patch
import pytest
from saya_app.services.qq_expressions import labelled_speech,speech_span
from saya_app.services.qq_voice import QQVoice
from test_qq_expressions import lab,row
from test_qq_voice import FakeTTS,submit


@pytest.mark.parametrize('label',['语音：','语音版：','**语音：**','**语音**：','### 语音：','朗读内容：'])
@pytest.mark.parametrize('wrapper',[lambda s:s,lambda s:'“'+s+'”',lambda s:'**'+s+'**'])
def test_labelled_section_proves_exact_inner_offsets_and_omits_label(label,wrapper):
    sentence='辛苦了，我在这里陪着你。'
    body='先说较长的说明。\n\n'+label+wrapper(sentence)
    value=labelled_speech(body,120);assert value['status']=='valid'
    span=value['span'];assert body[span['start']:span['end']]==sentence
    assert speech_span(body,sentence,120)==span
    assert speech_span(body,'先说较长的说明。',120) is None


def test_duplicate_text_is_resolved_by_the_unique_section_not_first_occurrence():
    sentence='我在这里陪着你。';body=sentence+'其他说明。\n\n语音：'+sentence
    value=labelled_speech(body,120);assert value['status']=='valid'
    assert value['span']['start']==body.rindex(sentence)
    assert speech_span(body,sentence,120)==value['span']


@pytest.mark.parametrize('body,status',[
    ('说明。\n\n语音：','empty'),('语音：\n\n这是另一个段落。','empty'),('说明。\n\n语音：'+'长话。'*60,'too_long'),
    ('说明。\n语音：第一段。\n语音版：第二段。','ambiguous'),
    ('说明。\n语音：看 https://example.invalid。','unsafe'),
    ('```text\n语音：代码里面。\n```','unsafe'),
    ('> 语音：引用内容。','absent'),('说明里面提到语音：不是独立段。','absent')])
def test_unsafe_ambiguous_or_quoted_sections_are_not_fabricated(body,status):
    assert labelled_speech(body,120)['status']==status


@pytest.mark.parametrize('failure',[False,True])
def test_actual_queue_reads_declared_section_even_if_model_picks_first_sentence_or_fails(tmp_path,failure):
    async def run():
        first='第一句是普通说明，不是指定的语音。';spoken='先歇一会儿，我会陪着你。'
        body=first+'这是详细内容。'*22+'\n\n语音：'+spoken
        b,c=await lab(tmp_path,answer=body,choice={'kind':'none','voice':True,'speech_anchor':first})
        b.dialogue_experience.configure('20000',0,{'enabled':True,'concise_replies':False,'short_voice':True})
        tts=FakeTTS();b.voice=QQVoice(b,tmp_path,tts)
        if failure:
            async def fail(*args):raise TimeoutError()
            b.expressions._select=fail
        try:
            submit(b,'最后用语音安慰我一句，这次不要保存记忆');await b.queue.join()
            assert len(c.voices)==1 and tts.calls[0][0]==spoken and not c.reactions
            p=json.loads(row(b)['plan']);assert p['speech_source']=='labelled_segment'
            assert p['speech_span']['start']==body.index(spoken)
            with b.store.connect() as db:metrics=json.loads(db.execute('select metrics from qq_experience_metrics').fetchone()[0])
            assert metrics['speech_source']=='labelled_segment'
            assert ''.join(x[1] for x in c.sent)==body
        finally:await b.stop()
    asyncio.run(run())


@pytest.mark.parametrize('mode',['auto','voice'])
@pytest.mark.parametrize('section',['语音：','语音：'+'很长的话。'*50,'语音：一。\n语音：二。'])
def test_invalid_declared_section_never_falls_back_to_other_text(tmp_path,mode,section):
    async def run():
        body='第一句仍然不能替代无效的语音段。\n\n'+section
        b,c=await lab(tmp_path,answer=body,mode=mode,choice={'kind':'none','voice':True})
        b.dialogue_experience.configure('20000',0,{'enabled':True})
        tts=FakeTTS();b.voice=QQVoice(b,tmp_path,tts)
        try:
            submit(b,'请发语音');await b.queue.join()
            assert c.sent and not c.voices and not tts.calls
        finally:await b.stop()
    asyncio.run(run())


def test_section_label_does_not_force_voice_without_semantic_or_explicit_intent(tmp_path):
    async def run():
        b,c=await lab(tmp_path,answer='说明。\n\n语音：我在。',choice={'kind':'none','voice':False})
        b.dialogue_experience.configure('20000',0,{'enabled':True})
        tts=FakeTTS();b.voice=QQVoice(b,tmp_path,tts)
        try:
            submit(b,'随便聊聊');await b.queue.join();assert not c.voices and not tts.calls
        finally:await b.stop()
    asyncio.run(run())


@pytest.mark.parametrize('expiry_delta,required',[(300,True),(-1,False)])
def test_unexpired_declared_section_requires_a_compatible_rollback_reader(tmp_path,expiry_delta,required):
    from saya_app.services.qq_rollback import memory_rollback_requirements
    directory=tmp_path/'knowledge_base/qq';directory.mkdir(parents=True)
    with sqlite3.connect(directory/'bridge.sqlite3') as db:
        db.execute('create table qq_expression_deliveries(plan text,expires real)')
        db.execute('insert into qq_expression_deliveries values(?,?)',(json.dumps({'speech_source':'labelled_segment'}),time.time()+expiry_delta))
    assert ('qq_speech_section_v1' in memory_rollback_requirements(tmp_path)) is required
