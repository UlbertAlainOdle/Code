"""QQ delivery cues are silent; parentheses containing actual prose stay audible."""
import asyncio,json
import pytest
from saya_app.services.qq_voice import spoken_body,QQVoice
from saya_app.services.speech_text import clean_speech
from test_qq_expressions import lab,row
from test_qq_voice import FakeTTS,submit


@pytest.mark.parametrize('cue',['很轻','轻声','轻声说','用温柔的语气说','语气很轻','轻轻地',
    '压低声音','停顿','轻声，慢慢地','轻声、温柔地'])
@pytest.mark.parametrize('brackets',[('（','）'),('(',')')])
def test_known_complete_delivery_cues_are_not_spoken(cue,brackets):
    text=brackets[0]+cue+brackets[1]+'今晚辛苦了，先好好休息。'
    assert spoken_body(text)=='今晚辛苦了，先好好休息。'
    assert clean_speech(text)==text  # Original shared semantics are unchanged.


@pytest.mark.parametrize('content',['很轻的箱子','别忘了喝水','如果困了就睡','我明天再来',
    '2026年','音量低于30分贝','x+1','a+b=2','张三','轻声是什么意思','轻声，也记得喝水'])
def test_actual_parenthesized_prose_names_and_math_are_preserved(content):
    text='补充（'+content+'）。';assert spoken_body(text)==text


def test_unwrapped_words_nested_prose_and_emoticons_keep_existing_rules():
    assert spoken_body('这只箱子很轻。')=='这只箱子很轻。'
    assert spoken_body('说明（这是（很轻的箱子））。')=='说明（这是（很轻的箱子））。'
    assert spoken_body('（很轻）你好。(^_^) [微笑] 😊')=='你好。'
    assert spoken_body(spoken_body('（轻声）晚安。'))=='晚安。'


def test_real_queue_selects_original_labelled_span_but_synthesizes_only_spoken_words(tmp_path):
    async def run():
        prose='先说明今晚的放松安排。'+'这里保留较长的文字建议。'*15
        spoken='今晚辛苦了，先好好休息。';section='（很轻）'+spoken
        body=prose+'\n\n语音：'+section
        b,c=await lab(tmp_path,answer=body,choice={'kind':'none','voice':True,'speech_anchor':section})
        b.dialogue_experience.configure('20000',0,{'enabled':True,'concise_replies':False,'short_voice':True})
        tts=FakeTTS();b.voice=QQVoice(b,tmp_path,tts)
        try:
            submit(b,'最后用语音安慰我一句，这次不要保存记忆');await b.queue.join()
            assert len(c.voices)==1 and tts.calls[0][0]==spoken
            assert ''.join(x[1] for x in c.sent)==body
            plan=json.loads(row(b)['plan']);span=plan['speech_span']
            assert plan['speech_source']=='labelled_segment' and body[span['start']:span['end']]==section
        finally:await b.stop()
    asyncio.run(run())
