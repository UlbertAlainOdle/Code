from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timedelta
import json
from pathlib import Path
import threading
from unittest.mock import patch

import pytest

from core_services import KnowledgeBase
from saya_app.services import dream,echo
from saya_app.services.chat_memory_writes import journal

DATE='2026-11-05'
NOW=datetime(2026,11,6,12)


@pytest.fixture
def lab(tmp_path):
    base=tmp_path/'knowledge_base';kb=KnowledgeBase(str(base))
    source=base/'session_lab.jsonl'
    rows=[{'time':DATE+' 10:00:00','role':'user','content':'虚构青杯偏好蓝色。'},
          {'time':DATE+' 10:01:00','role':'ai','content':'好的，虚构青杯蓝色。'}]
    source.write_text('\n'.join(json.dumps(row,ensure_ascii=False) for row in rows)+'\n',encoding='utf-8')
    return base,kb,source


def model(prompt):
    return json.dumps({'theme':'虚构青杯偏好蓝色。','evidence':['虚构青杯偏好蓝色']}) if 'memory consolidator' in prompt else '今天提起「虚构青杯偏好蓝色」，记下来了。'


def run(lab,invoke=model,**kwargs):
    base,kb,_=lab
    return dream.run(base,kb,invoke,target_date=DATE,now=NOW,**kwargs)


def test_repeat_and_restart_do_not_generate_or_save_again(lab):
    base,kb,_=lab;first=run(lab)
    assert first['ran'] and first['calls']==2 and first['diary_saved']
    restarted=KnowledgeBase(str(base))
    with patch.object(restarted,'add_memory',side_effect=AssertionError('repeat')):
        again=dream.run(base,restarted,lambda p:pytest.fail('repeat model'),DATE,NOW)
    assert again['ran'] and again['calls']==0 and again['theme_memory_id']==first['theme_memory_id']
    assert len(restarted.memories)==1


def test_parallel_waiter_only_reads_pending(lab):
    entered=threading.Event();release=threading.Event()
    def blocking(prompt):entered.set();assert release.wait(5);return model(prompt)
    with ThreadPoolExecutor(2) as pool:
        first=pool.submit(run,lab,blocking);assert entered.wait(5)
        try:
            second=pool.submit(run,lab,lambda p:pytest.fail('concurrent model')).result(5)
            assert not second['ran'] and second['calls']==0
        finally:release.set()
        assert first.result(5)['diary_saved']
    assert len(lab[1].memories)==1


@pytest.mark.parametrize('change',['source','epoch','authorization'])
def test_change_during_model_prevents_old_theme_and_diary(lab,change):
    base,kb,source=lab;allowed=[True]
    def invoke(prompt):
        if change=='source':source.write_text('',encoding='utf-8')
        elif change=='epoch':kb.memory_use.advance()
        else:allowed[0]=False
        return model(prompt)
    result=run(lab,invoke,authorized=lambda:allowed[0])
    assert not result['ran'] and not result['diary_saved'] and not kb.memories
    assert dream.read_diary(base,DATE) is None


def test_second_step_invalidation_does_not_claim_rollback_of_first(lab):
    base,kb,_=lab
    def invoke(prompt):
        if 'memory consolidator' not in prompt:kb.memory_use.advance()
        return model(prompt)
    result=run(lab,invoke)
    assert result['theme_memory_id'] and not result['diary_saved'] and not result['ran']
    assert not kb.memory_allowed_for_use(kb._find_memory(result['theme_memory_id']))


def test_known_staged_diary_failure_repairs_files_without_model_or_memory_write(lab):
    base,kb,_=lab
    with patch.object(dream,'save_diary',side_effect=OSError('file busy')):first=run(lab)
    assert first['theme_memory_id'] and not first['diary_saved']
    with patch.object(kb,'add_memory',side_effect=AssertionError('repeat')):
        second=run(lab,lambda p:pytest.fail('repeat model'))
    assert second['diary_saved'] and second['calls']==0 and len(kb.memories)==1
    assert dream.read_diary(base,DATE)


def test_written_diary_then_response_failure_does_not_overwrite_on_retry(lab):
    base,_,_=lab;save=dream.save_diary
    def fail(*args):save(*args);raise OSError('after rename')
    with patch.object(dream,'save_diary',fail):first=run(lab)
    original=Path(dream._diary_path(base,DATE)).read_bytes()
    with patch.object(dream,'save_diary',side_effect=AssertionError('rewrite')):second=run(lab,lambda p:pytest.fail('repeat'))
    assert second['diary_saved'] and Path(dream._diary_path(base,DATE)).read_bytes()==original


@pytest.mark.parametrize('change',['source','epoch','diary'])
def test_repair_refuses_changed_source_permission_or_diary(lab,change):
    base,kb,source=lab
    with patch.object(dream,'save_diary',side_effect=OSError('busy')):run(lab)
    if change=='source':source.write_text('',encoding='utf-8')
    elif change=='epoch':kb.memory_use.advance()
    else:
        path=Path(dream._diary_path(base,DATE));path.parent.mkdir(exist_ok=True);path.write_text('{"manual":"keep"}')
    result=run(lab,lambda p:pytest.fail('repeat'))
    assert result['operations']['dream_diary']['items'][0]['recovery_blocked']
    assert not result['diary_saved'] and len(kb.memories)==1
    if change=='diary':assert json.loads(path.read_text())=={'manual':'keep'}


def test_user_edits_diary_while_model_waits_is_not_overwritten(lab):
    base,_,_=lab
    def invoke(prompt):
        if 'memory consolidator' not in prompt:
            target=Path(dream._diary_path(base,DATE));target.parent.mkdir(exist_ok=True);target.write_text('{"manual":"keep"}')
        return model(prompt)
    result=run(lab,invoke)
    assert not result['diary_saved'] and json.loads(Path(dream._diary_path(base,DATE)).read_text())=={'manual':'keep'}


@pytest.mark.parametrize('change',['source','memory_delete','policy'])
def test_old_diary_and_theme_are_excluded_after_source_invalidation(lab,change):
    base,kb,source=lab;result=run(lab);target=Path(dream._diary_path(base,DATE));original=target.read_bytes()
    memory=kb._find_memory(result['theme_memory_id'])
    assert kb.memory_allowed_for_use(memory) and dream.read_diary(base,DATE)
    if change=='source':source.write_text('',encoding='utf-8')
    elif change=='memory_delete':kb.delete_memory(memory['id'])
    else:kb.set_memory_use_policy(memory['id'],True)
    assert dream.read_diary(base,DATE) is None and dream.latest_diary(base,NOW) is None
    assert not dream.list_diaries(base) and not kb.memory_allowed_for_use(memory)
    assert target.read_bytes()==original
    projects=type('Projects',(),{'list_projects':lambda self:[]})()
    assert not echo.build_candidates(kb,projects,{'echo_source_dream':True},NOW)


def test_non_day_append_does_not_invalidate_old_diary(lab):
    base,_,source=lab;run(lab)
    with source.open('a',encoding='utf-8') as f:f.write(json.dumps({'time':'2026-11-06 01:00:00','role':'user','content':'新一天'})+'\n')
    assert dream.read_diary(base,DATE)


def test_unknown_theme_survives_expiry_and_does_not_repeat(lab):
    _,kb,_=lab;actual=kb.add_memory
    def fail(*a,**kw):actual(*a,**kw);raise OSError('after save')
    with patch.object(kb,'add_memory',fail):first=run(lab)
    assert first['operations']['dream_theme']['items'][0]['state']=='unknown'
    ledger=journal(kb);ledger.clock=lambda:10**12
    ledger.run('b'*32,'other','other','capture',[{}],verify=lambda db:True,valid=lambda p:True,write=lambda p:{'state':'not_saved'},retention=1)
    with patch.object(kb,'add_memory',side_effect=AssertionError('repeat')):again=run(lab,lambda p:pytest.fail('repeat'))
    assert again['operations']['dream_theme']['items'][0]['state']=='unknown' and len(kb.memories)==1


def test_other_purposes_and_control_receipts_do_not_become_dream_evidence(lab):
    base,kb,source=lab;row={'time':DATE+' 10:00:00','role':'user','content':'秘密测试来源'}
    for name in ['session_qq_lab.jsonl','session_mc_lab.jsonl','session_listener.jsonl']:
        (base/name).write_text(json.dumps({**row,'listener':'user'} if 'listener' in name else row))
    with source.open('a',encoding='utf-8') as f:f.write(json.dumps({**row,'content_kind':'control_receipt'})+'\n')
    assert all('秘密' not in row['content'] for row in dream.collect_day(base,DATE))


def test_bad_dates_and_corrupt_state_do_not_write_defaults(lab):
    base,_,_=lab
    with pytest.raises(ValueError):dream.read_diary(base,'../bad')
    (base/'dream_state.json').write_text('broken')
    with pytest.raises(ValueError,match='unavailable'):run(lab,lambda p:pytest.fail('model'))
    assert (base/'dream_state.json').read_text()=='broken'


def test_revoked_background_permission_blocks_file_only_repair(lab):
    with patch.object(dream,'save_diary',side_effect=OSError('busy')):run(lab)
    with patch.object(dream,'save_diary',side_effect=AssertionError('revoked repair')):
        report=run(lab,lambda p:pytest.fail('repeat'),authorized=lambda:False)
    assert not report['diary_saved'] and report['operations']['dream_diary']['items'][0]['recovery_blocked']


def test_legacy_or_edited_diary_is_retained_but_not_used(lab):
    base,_,_=lab;path=Path(dream._diary_path(base,DATE));path.parent.mkdir()
    path.write_text(json.dumps({'date':DATE,'entry':'旧的未绑定日记'}))
    assert dream.read_diary(base,DATE) is None and path.exists()
    run(lab);payload=json.loads(path.read_text(encoding='utf-8'));payload['entry']='手工替换的无证据正文'
    path.write_text(json.dumps(payload))
    assert dream.read_diary(base,DATE) is None and path.exists()


def test_http_result_only_queries_original_steps_and_does_not_repair(lab,monkeypatch):
    import asyncio
    import httpx
    from fastapi import FastAPI
    from saya_app.routers import companion
    base,kb,_=lab
    with patch.object(dream,'save_diary',side_effect=OSError('busy')):run(lab)
    monkeypatch.setattr(companion,'_BASE',str(base));monkeypatch.setattr(companion,'kb_system',kb)
    app=FastAPI();app.include_router(companion.router)
    async def check():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://lab') as client:
            reply=await client.get('/api/companion/dream/result',params={'date':DATE})
            assert reply.status_code==200 and '青杯' not in reply.text
            assert reply.json()['operations']['dream_diary']['items'][0]['file_pending']
            assert (await client.get('/api/companion/dream/result',params={'date':'2026-11-04'})).status_code==404
            assert (await client.get('/api/companion/dream/result',params={'date':'../bad'})).status_code==400
    with patch.object(dream,'save_diary',side_effect=AssertionError('GET wrote')):asyncio.run(check())


def test_background_setting_revoked_then_restored_does_not_revive_inflight_plan(lab):
    _,kb,_=lab
    def invoke(prompt):
        kb.update_settings({'dream_enabled':True});kb.update_settings({'dream_enabled':False})
        return model(prompt)
    result=run(lab,invoke)
    assert not result['theme_memory_id'] and not result['diary_saved'] and not kb.memories


def test_copying_valid_diary_under_another_date_does_not_change_its_identity(lab):
    base,_,_=lab;run(lab)
    other=Path(dream._diary_path(base,'2026-11-04'))
    other.write_bytes(Path(dream._diary_path(base,DATE)).read_bytes())
    assert dream.read_diary(base,DATE) and dream.read_diary(base,'2026-11-04') is None
