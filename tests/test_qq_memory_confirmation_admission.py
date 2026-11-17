import asyncio
from unittest.mock import patch
import pytest
from test_qq_memory_reference_dialogue import lab
from test_qq_memory_candidate_quotes import responder,send
from test_qq_delivery import event


def test_fast_second_confirmation_gets_original_receipt_without_second_write(tmp_path):
    async def run():
        async with lab(tmp_path) as (b,s,k,m,manager,q,scope,sid,tag):
            await b.update_controls(b.controls()['settings']|{'cooldown_seconds':60})
            preview=m.references.sets.preview(q,sid,m.references.options(q,sid))
            b.action_proposals.delivered(scope,sid,str(preview));responder(b,m)
            await send(b,'记住',811)
            assert len(k.memories)==2
            original=b.client.sent[-1][1]
            with patch.object(k,'capture_explicit_memory',side_effect=AssertionError('repeat storage')):
                await send(b,'记住',812)
            assert b.client.sent[-1][1]==original and len(k.memories)==2
            assert b.settings.cooldown_seconds==60
    asyncio.run(run())


@pytest.mark.parametrize('state',['prepared','superseded','rejected','expired'])
def test_no_delivered_current_proposal_cannot_bypass_normal_cooldown(tmp_path,state):
    async def run():
        async with lab(tmp_path) as (b,s,k,m,manager,q,scope,sid,tag):
            await b.update_controls(b.controls()['settings']|{'cooldown_seconds':60})
            preview=m.references.preview(q,sid,m.references.options(q,sid)[0])
            with b.store.connect() as db:db.execute('update qq_action_proposals set state=?',(state,))
            result,row=b.store.admit(q,b.settings);assert row
            b.store.set_state(row,'processed')
            assert b.submit(event('记住',813))[0]=='rate_limited'
            assert not k.memories
    asyncio.run(run())


def test_scoped_admission_is_readonly_and_does_not_authorize_other_session_or_search(tmp_path):
    async def run():
        async with lab(tmp_path) as (b,s,k,m,manager,q,scope,sid,tag):
            preview=m.references.preview(q,sid,m.references.options(q,sid)[0])
            b.action_proposals.delivered(scope,sid,str(preview))
            with b.store.connect() as db:before=db.execute('select * from qq_action_proposals').fetchall()
            gate=b.action_proposals.confirmation_admissible
            assert gate(scope,sid,'记住',b.controls()['revision'])
            assert not gate(scope,'session_qq_other.jsonl','记住',b.controls()['revision'])
            assert not gate(scope,sid,'普通聊天',b.controls()['revision'])
            assert not gate(scope,sid,'记住','stale-revision')
            with b.store.connect() as db:assert db.execute('select * from qq_action_proposals').fetchall()==before
            b.action_proposals.ask(scope,sid,'search_on',{},'开启搜索吗？',b.controls()['revision'])
            b.action_proposals.delivered(scope,sid,'开启搜索吗？')
            assert not gate(scope,sid,'记住',b.controls()['revision'])
            assert not k.memories
    asyncio.run(run())


def test_queue_capacity_and_duplicate_inbound_dedupe_still_apply(tmp_path):
    async def run():
        async with lab(tmp_path) as (b,s,k,m,manager,q,scope,sid,tag):
            await b.update_controls(b.controls()['settings']|{'cooldown_seconds':60})
            b.settings=b.settings.model_copy(update={'queue_size':1})
            preview=m.references.preview(q,sid,m.references.options(q,sid)[0]);b.action_proposals.delivered(scope,sid,str(preview))
            assert b.submit(event('记住',814))[0]=='queued'
            assert b.submit(event('记住',814))[0]=='duplicate'
            assert b.submit(event('记住',815))[0]=='busy'
    asyncio.run(run())
