import asyncio
import json
from dataclasses import replace
from unittest.mock import patch

import httpx
import pytest

from core_services import KnowledgeBase, MemoryManager
from saya_app.services.conversation import conversation_service
from saya_app.services.history_output import HistoryLabelStream, strip_history_labels, history_safe_events
from saya_app.services.qq_presentation import clean_control_output, clean_assistant_history
from test_qq_chat_runtime import server
from test_qq_memory_management import lab

LABEL = '[History sent_at=2026-11-04T02:56:32+08:00; timezone=Asia/Shanghai]'


@pytest.mark.parametrize('label', [LABEL, LABEL.replace('2026-11-04T02:56:32+08:00', 'unknown'),
    LABEL.replace('+08:00', 'Z'), LABEL.replace('32+08', '32.123+08')])
def test_owned_labels_hidden_without_removing_clock_content(label):
    assert strip_history_labels(label + '正文。') == '正文。'
    assert clean_control_output(' \n' + label + '正文。[Mood: curious]') == '正文。'
    assert strip_history_labels('正文\n' + label + '\n下一句') == '正文\n\n下一句'


@pytest.mark.parametrize('text', [
    '普通时间 2026-11-04T02:56:32+08:00', '[History of tea]', '[99]',
    '说明 `' + LABEL + '`。', '```text\n' + LABEL + '\n```\n结束',
    '~~~text\n' + LABEL + '\n~~~\n结束', '    ' + LABEL + '\n',
    '\t' + LABEL, '引用示例：' + LABEL, '[History sent_at=not-a-date; timezone=Other]'])
def test_literal_examples_and_ordinary_brackets_survive(text):
    assert strip_history_labels(text) == text


def test_math_literal_and_malformed_header_are_preserved():
    text = '$$\n' + LABEL + '\n$$\n' + LABEL + '正文'
    assert strip_history_labels(text) == '$$\n' + LABEL + '\n$$\n正文'
    assert strip_history_labels('[History sent_at=not-a-date\n后文') == '[History sent_at=not-a-date\n后文'


def test_every_single_cut_and_one_character_chunks_match_nonstreaming():
    raw = LABEL + '正文。\n```text\n' + LABEL + '\n```\n' + LABEL + '末句'
    expected = '正文。\n```text\n' + LABEL + '\n```\n末句'
    for split in range(len(raw) + 1):
        stream = HistoryLabelStream()
        assert stream.feed(raw[:split]) + stream.feed(raw[split:]) + stream.finish() == expected
    stream = HistoryLabelStream()
    assert ''.join(stream.feed(c) for c in raw) + stream.finish() == expected


def test_unfinished_owned_label_is_hidden_but_normal_short_bracket_is_kept():
    assert strip_history_labels('正文\n[History sent_at=2026-11-04T') == '正文\n'
    assert strip_history_labels('[Hi') == '[Hi'
    stream = HistoryLabelStream()
    assert stream.feed('这是一段立即显示的正文') == '这是一段立即显示的正文'
    assert not stream.pending
    stream.feed('\n' + '[History sent_at=' + '2' * 1000)
    assert len(stream.pending) <= 256 and len(stream.probe) <= 256


def test_assistant_history_is_ephemeral_and_does_not_remove_user_question():
    old = ['Doctor: ' + LABEL, 'Saya: ' + LABEL + '旧回复']
    assert clean_assistant_history(old) == [old[0], 'Saya: 旧回复']
    assert old[1].endswith(LABEL + '旧回复')


def event(text):
    return 'event: text\ndata: ' + json.dumps({'text': text}, ensure_ascii=False) + '\n\n'


def text_of(frames):
    return ''.join(json.loads(x.split('data: ', 1)[1])['text'] for x in frames if x.startswith('event: text\n'))


def test_sse_wrapper_preserves_metadata_and_closes_original():
    closed = []
    status = 'event: assistant_status\ndata: {"phase":"completed"}\n\n'
    async def source():
        try:
            for ch in LABEL + '正文。': yield event(ch)
            yield status
        finally: closed.append(True)
    async def run():
        frames = [x async for x in history_safe_events(source())]
        assert text_of(frames) == '正文。' and frames[-1] == status
    asyncio.run(run())
    assert closed == [True]


def test_ordinary_pending_prefix_precedes_completion_event():
    status = 'event: assistant_status\ndata: {"phase":"completed"}\n\n'
    async def source():
        yield event('[Hi')
        yield status
    async def run():
        frames = [x async for x in history_safe_events(source())]
        assert text_of(frames) == '[Hi' and frames[-1] == status
    asyncio.run(run())


@pytest.mark.parametrize('failed', [False, True])
def test_failure_or_cancellation_does_not_flush_pending_text(failed):
    closed = []
    async def source():
        try:
            yield event('[His')
            if failed: yield 'event: assistant_status\ndata: {"phase":"failed"}\n\n'
            else: raise asyncio.CancelledError()
        finally: closed.append(True)
    async def run():
        frames = []
        try:
            async for x in history_safe_events(source()): frames.append(x)
        except asyncio.CancelledError:
            assert not failed
        assert not text_of(frames)
    asyncio.run(run())
    assert closed == [True]


def test_cached_qq_parts_and_transport_do_not_expose_old_prefix(tmp_path):
    from saya_app.services import qq_delivery as delivery
    from saya_app.services.qq_bridge import QQStore, QQMessage, OneBotClient
    from test_qq_delivery import Client, config
    store = QQStore(tmp_path / 'qq.sqlite3'); delivery.initialize(store)
    msg = QQMessage('k', 's', '10000', '20000', '', '12', 'q', False)
    rid = delivery.create(store, msg, 's', '旧正文。', 120)
    with store.connect() as db:
        db.execute("update qq_delivery_parts set text=? where result_id=?", (LABEL + '待发正文。', rid))
    async def run():
        client = Client()
        await delivery.send_batch(store, client, msg, 's', rid, 3, True)
        assert len(client.sent) == 1 and LABEL not in str(client.sent)
        payloads = []
        def post(req):
            payloads.append(json.loads(req.content))
            return httpx.Response(200, json={'status':'ok','retcode':0,'data':{'message_id':5}})
        wire = OneBotClient(config()); await wire.http.aclose()
        wire.http = httpx.AsyncClient(transport=httpx.MockTransport(post))
        try:
            await wire.send(msg, LABEL + '新正文。[Mood: curious]')
            assert payloads[0]['message'] == [{'type':'text','data':{'text':'新正文。'}}]
        finally: await wire.close()
    asyncio.run(run())


@pytest.mark.parametrize('qq', [False, True])
def test_actual_generator_stream_and_saved_reply_hide_label_but_model_keeps_history_times(server, tmp_path, qq):
    manager = MemoryManager(str(tmp_path / 'chats')); kb = KnowledgeBase(str(tmp_path / 'kb'))
    manager.legacy_sessions_file = str(tmp_path / 'no-legacy.json')
    sid = 'session_qq_history_guard.jsonl' if qq else 'session_web_history_guard.jsonl'
    rows = [{'role':'user','content':'旧问题','time':'2026-10-30 09:00:00'},
            {'role':'ai','content':'旧回复','time':'2026-10-30 09:00:05'}]
    (tmp_path / 'chats' / sid).write_text('\n'.join(json.dumps(x) for x in rows) + '\n', encoding='utf8')
    seen = []
    def model(req):
        seen.append(json.loads(req.content))
        chunks = [{'choices':[{'delta':{'content':c},'finish_reason':None}]} for c in LABEL + '新正文。']
        chunks.append({'choices':[{'delta':{},'finish_reason':'stop'}]})
        return httpx.Response(200, text=''.join('data: '+json.dumps(x)+'\n\n' for x in chunks)+'data: [DONE]\n\n')
    async def no_candidates(*a, **kw): return []
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(model)) as api:
            with patch.object(server, '_get_api_httpx_client', lambda:api):
                req = server.ChatWithMotion(user_input='你好', session_id=sid, model='api', api_key='fake',
                    api_url='http://mock/v1', agent_mode=False, recall_mode='off', memory_write_enabled=False, web_search_mode='off')
                response = await server._chat_stream_single(req, channel_turn={'history':['Doctor: 旧问题','Saya: 旧回复'],
                    'shared':True,'is_owner':True,'sender_id':'20000','history_times':[]} if qq else None)
                frames = [x async for x in response.body_iterator]
                assert text_of(frames) == '新正文。'
                complete = [json.loads(x.split('data: ',1)[1]) for x in frames if x.startswith('event: answer_complete\n')]
                assert all(LABEL not in x['text'] for x in complete)
    with (patch.object(server,'kb_system',kb), patch.object(server,'memory_manager',manager),
          patch.object(server,'refresh_daily_activity_memory',lambda:None),
          patch.object(server,'maybe_create_autonomous_memory_candidates',no_candidates),
          patch.object(conversation_service,'save_credentials',lambda **kw:None),
          patch.object(server.companion_router,'check_conflict_for_memory',return_value=None)):
        asyncio.run(run())
    assert any('[History sent_at=2026-10-30' in str(x['messages']) for x in seen)
    saved = manager.load_session_full(sid, make_current=False)
    assert saved[-1]['content'] == '新正文。'


def test_memory_detail_reports_existing_score_without_mutation(lab):
    _, kb, service, owner, memory = lab
    from saya_app.services.memory_versions import record_version
    before = record_version(memory)
    view = service.detail(owner, memory['id'])['memory']
    assert view['importance_score'] == memory['importance_score']
    assert view['memory_tier'] == kb._memory_tier(memory)
    assert view['base_priority_score'] == kb.memory_priority_score(memory)
    assert record_version(memory) == before
    reply = service.command(owner, '/memory show ' + memory['id'])
    assert '当前归档：整理档' in reply and '重要性权重' in reply and '无查询优先分' in reply
    assert before in reply
