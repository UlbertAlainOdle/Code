"""New experience controls share owner/CAS policy and truthful live capabilities."""
import asyncio,json
from dataclasses import replace
import pytest
from saya_app.services.qq_natural_actions import parse_action
from saya_app.services.qq_capabilities import snapshot,prompt
from saya_app.services.qq_help import command_help
from saya_app.services.qq_bridge import normalize_event
from saya_app.services.qq_responses import ControlReceipt,dialogue_body
from test_qq_delivery import event
from test_qq_task_scheduler import make,submit
from test_qq_expressions import lab
from test_qq_voice import FakeTTS
from saya_app.services.qq_voice import QQVoice


@pytest.mark.parametrize('text,kind,value',[
    ('开启对话体验','experience_master',True),('请关闭篇幅引导','concise_replies',False),
    ('把精简回复打开吧','concise_replies',True),('打开简洁回复','concise_replies',True),
    ('短句语音关掉','short_voice',False),('帮我开启长文短语音','short_voice',True)])
def test_registered_explicit_experience_actions(text,kind,value):
    assert parse_action(text)=={'id':kind,'value':value}


@pytest.mark.parametrize('text',['这次简短点','他让我开启短句语音','不要开启篇幅引导',
    '“开启对话体验”是什么意思','开启所有体验权限','这次关闭短句语音','如果关闭短句语音会怎样'])
def test_one_turn_quoted_negated_or_conditional_text_is_not_a_persistent_switch(text):
    assert parse_action(text) is None


def test_owner_subcomponent_changes_preserve_master_other_policies_and_history(tmp_path):
    async def run():
        seen=[]
        async def model(message,*args):seen.append(message.text);return '聊聊电影。'
        b=make(tmp_path,model);await b.start()
        before=b.controls()
        try:
            b.dialogue_experience.configure('20000',0,{'enabled':False,'concise_replies':False,'short_voice':False})
            submit(b,'开启短句语音');await b.queue.join()
            assert b.dialogue_experience.state()['values']=={'enabled':False,'concise_replies':False,'short_voice':True}
            assert '当前不生效' in b.client.sent[-1][1] and seen==[]
            submit(b,'开启对话体验',2);await b.queue.join()
            submit(b,'开启篇幅引导，顺便聊聊电影',3);await b.queue.join()
            assert all(b.dialogue_experience.state()['values'].values()) and seen==['聊聊电影']
            history,_=b.store.conversation('10000:private:20000:shared')
            assert '开启篇幅引导' not in str(history) and '聊聊电影' in str(history)
            assert all(b.controls()[key]==before[key] for key in ['settings','revision','inherit_fields','shared'])
        finally:await b.stop()
    asyncio.run(run())


@pytest.mark.parametrize('user,group',[('30000',''),('20000','90000')])
def test_other_user_or_owner_in_group_cannot_persist_experience_policy(tmp_path,user,group):
    async def run():
        async def model(*args):raise AssertionError('must not call model')
        b=make(tmp_path,model);await b.start()
        try:
            message=normalize_event(event('开启短句语音',group_id=int(group) if group else None,user_id=int(user)),b.settings)[0]
            before=b.dialogue_experience.state()
            receipt=await b.natural_actions.execute(message)
            assert isinstance(receipt,ControlReceipt) and '没有执行' in receipt and dialogue_body(receipt)==''
            assert b.dialogue_experience.state()==before
        finally:await b.stop()
    asyncio.run(run())


def test_capabilities_and_receipts_follow_actual_master_media_and_mode(tmp_path):
    async def run():
        b,c=await lab(tmp_path,mode='random')
        b.voice=QQVoice(b,tmp_path,FakeTTS())
        message=normalize_event(event('开启短句语音'),b.settings)[0]
        media_before=b.media.state();mode_before=b.expressions.state()
        try:
            b.dialogue_experience.configure('20000',0,{'enabled':True,'short_voice':False,'concise_replies':False})
            # The strategy saves successfully without opening the independent media switch.
            b.media.configure('20000',media_before['media_revision'],{'voice_reply_enabled':False},confirmed=True)
            disabled_media=b.media.state()
            receipt=await b.natural_actions.execute(message)
            assert '总开关仍关闭' in receipt
            assert b.media.state()==disabled_media and b.expressions.state()==mode_before
            capabilities=snapshot(b,message)
            assert capabilities['dialogue_experience']['effective_short_voice']
            assert not capabilities['voice_output']['available']
            assert '20000' not in json.dumps(capabilities)
            assert '短句语音' in prompt(capabilities)
            receipt=await b.natural_actions.execute(message,'关闭短句语音')
            assert '关闭所有语音' in receipt and not snapshot(b,message)['dialogue_experience']['short_voice']
            receipt=await b.natural_actions.execute(message,'关闭对话体验')
            assert '分项选择保留' in receipt
            assert not snapshot(b,message)['dialogue_experience']['effective_short_voice']
            assert b.media.state()==disabled_media and b.expressions.state()==mode_before
        finally:await b.stop()
    asyncio.run(run())


@pytest.mark.parametrize('priority',[True,False])
def test_help_is_shared_truthful_and_does_not_enter_dialogue(tmp_path,priority):
    async def run():
        async def model(*args):raise AssertionError('help must not call model')
        b=make(tmp_path,model);await b.start()
        message=normalize_event(event('/help'),b.settings)[0]
        expected=command_help(b,message)
        try:
            if not priority:
                b.settings=b.settings.model_copy(update={'task_control_enabled':False})
                expected=command_help(b,message)
            submit(b,'/help');await b.queue.join();await b.task_controls.join()
            actual=''.join(x[1] for x in b.client.sent)
            assert expected in actual or all(line in actual for line in expected.splitlines())
            assert '/experience short-voice on/off' in actual and '只能文字' not in actual
            assert not b.store.conversation('10000:private:20000:shared')[0]
            other=command_help(b,replace(message,user_id='30000'))
            assert '/experience status' in other and '/experience on/off' not in other
        finally:await b.stop()
    asyncio.run(run())
