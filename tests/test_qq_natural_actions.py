import asyncio
import pytest
from saya_app.services.qq_natural_actions import parse_action,control_alias
from test_qq_task_scheduler import make,submit


@pytest.mark.parametrize('text',['他说“打开语音”','不要打开语音','“开启自动搜索”是什么意思','这次用语音回答','发点语音来听听','打开所有权限'])
def test_quoted_negated_one_turn_or_unregistered_requests_do_not_change_policy(text):
    assert parse_action(text) is None


def test_explicit_natural_toggle_executes_and_mixed_chat_only_keeps_dialogue(tmp_path):
    async def run():
        seen=[]
        async def model(message,*args):seen.append(message.text);return '电影从这里聊起。'
        bridge=make(tmp_path,model);await bridge.start()
        try:
            submit(bridge,'关闭保存记忆，顺便聊聊电影');await bridge.queue.join()
            assert not bridge.settings.memory_write_enabled
            assert seen==['聊聊电影']
            history,_=bridge.store.conversation('10000:private:20000:shared')
            assert '关闭保存记忆' not in str(history) and '聊聊电影' in str(history)
            assert any('【操作回执】' in item[1] for item in bridge.client.sent)
        finally:await bridge.stop()
    asyncio.run(run())


def test_nonowner_and_group_cannot_change_global_policy(tmp_path):
    async def run():
        async def model(*a):raise AssertionError('permission failure must not call model')
        bridge=make(tmp_path,model);await bridge.start()
        try:
            submit(bridge,'关闭保存记忆',user=30000);await bridge.queue.join()
            assert bridge.settings.memory_write_enabled
            assert '没有执行' in bridge.client.sent[-1][1]
        finally:await bridge.stop()
    asyncio.run(run())


def test_natural_cancel_uses_priority_lane_and_never_runs_model(tmp_path):
    async def run():
        entered,release=asyncio.Event(),asyncio.Event()
        async def model(*a):entered.set();await release.wait();return 'LATE_CANARY'
        bridge=make(tmp_path,model);await bridge.start()
        try:
            submit(bridge,'慢任务');await entered.wait()
            assert submit(bridge,'取消当前任务',2)[0]=='queued'
            await bridge.task_controls.join();release.set();await bridge.queue.join()
            assert not any('LATE_CANARY' in p[1] for p in bridge.client.sent)
        finally:release.set();await bridge.stop()
    asyncio.run(run())
