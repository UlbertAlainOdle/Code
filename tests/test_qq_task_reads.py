import asyncio
from types import SimpleNamespace

import pytest

from saya_app.services.qq_tasks import TaskCancelled
from saya_app.services.web_research.search import BraveSearchService
from saya_app.services.web_research.deepseek import DeepSeekAPI
from saya_app.services.web_research.reader import WebReader
from saya_app.services.web_research import reader as reader_module
from saya_app.services.qq_chat import QQChatResponder
from test_qq_task_memory import fixture
from test_qq_capabilities import config, message


@pytest.mark.parametrize('provider',['brave','deepseek','reader'])
@pytest.mark.parametrize('cancel_when',['before','response'])
def test_real_network_adapters_do_not_issue_or_use_cancelled_reads(tmp_path,monkeypatch,provider,cancel_when):
    registry,task,guard,_=fixture(tmp_path)
    calls=[];closed=[]
    class Response:
        status_code=302 if provider=='reader' else 200
        headers={'Location':'https://example.com/second'}
        def close(self):closed.append(True)
        def json(self):pytest.fail('late response must not be parsed')
        def iter_content(self,**kwargs):pytest.fail('late response must not be read')
    class Session:
        def get(self,*args,**kwargs):
            calls.append(True);registry.cancel(task['id'],task['scope']);return Response()
        post=get
    monkeypatch.setattr(reader_module,'is_url_safe',lambda *a,**k:(True,'',None))
    if cancel_when=='before':registry.cancel(task['id'],task['scope'])
    with guard.bind(),pytest.raises(TaskCancelled):
        if provider=='brave':BraveSearchService(Session()).search('query','fake-key')
        elif provider=='deepseek':DeepSeekAPI(Session()).post('search',{'query':'query'},'fake-key')
        else:WebReader(session=Session())._safe_download('https://example.com/first')
    assert len(calls)==(0 if cancel_when=='before' else 1)
    assert bool(closed)==(cancel_when=='response')


def test_qq_adapter_discards_late_model_chunk_and_closes_iterator(tmp_path):
    async def run():
        registry,task,guard,_=fixture(tmp_path)
        closed=[]
        async def single(req,channel_turn):
            async def stream():
                try:
                    registry.cancel(task['id'],task['scope'])
                    yield 'event: answer_complete\ndata: {"text":"late synthetic answer"}\n\n'
                finally:closed.append(True)
            return SimpleNamespace(body_iterator=stream())
        reply=QQChatResponder(config(),SimpleNamespace,single,lambda:'api',lambda:{
            'configured':True,'api_key':'fake','api_url':'https://example.com','model':'fake'})
        with guard.bind(),pytest.raises(TaskCancelled):
            await reply(message(),[],'session_qq_test.jsonl')
        assert closed==[True]
    asyncio.run(run())
