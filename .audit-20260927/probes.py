import run_tests
import asyncio, importlib.util, io, json, sys, tempfile, zipfile
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace
from core_services import GoalManager, CheckInManager, MemoryManager, KnowledgeBase
from saya_app.services.backup_manager import BackupManager
from saya_app.services.web_research.citation_validator import validate_response_citations
from saya_app.services.web_research.models import WebSource
ROOT=run_tests.ROOT
results={}
with tempfile.TemporaryDirectory(dir=ROOT/'.audit-20260927') as temp:
    base=Path(temp)
    goals=GoalManager(str(base/'goals'))
    g=goals.add_goal('example')
    goals.update_goal(g['id'],{'status':'completed','achieved_at':'2026-01-01 10:00:00'})
    goals.update_goal(g['id'],{'status':'active'})
    goals.update_goal(g['id'],{'status':'completed'})
    results['recompleted_goal_date']=g['achieved_at']
    with patch('core_services.codecs.open',side_effect=OSError('simulated disk full')):
        created=goals.add_goal('lost after restart')
    results['failed_save_returns_created_goal']=bool(created.get('id'))
    results['failed_save_persisted']=any(x['title']=='lost after restart' for x in GoalManager(str(base/'goals')).goals)
    for label,claim,evidence in [
        ('negation','Orion supports offline mode [1].','Orion does not support offline mode.'),
        ('direction','Orion revenue increased by 20% in 2025 [1].','Orion revenue decreased by 20% in 2025.'),
        ('chinese','星辰软件支持离线模式[1]。','星辰软件不支持离线模式。')]:
        source=WebSource(1,'Fixture','https://example.invalid','example.invalid',status='read',text=evidence)
        result=validate_response_citations(claim,[source])
        results['citation_'+label]={'status':result['status'],'support':result['claims'][0]['citations'][0]['support']}
    k=base/'backup-kb'; k.mkdir()
    c=base/'backup-chats'; c.mkdir()
    manager=BackupManager(k,c,base/'backups')
    data=k/'knowledge.json'; data.write_text('{"version":1}',encoding='utf-8')
    original_hash=manager._sha256
    def racing_hash(path):
        value=original_hash(path)
        if path==data: data.write_text('{"version":2}',encoding='utf-8')
        return value
    with patch.object(manager,'_sha256',racing_hash):
        backup=manager.create_backup(['memories'])
    preview=manager.preview_restore(backup['backup_id'],['memories'])
    try:
        manager.restore(backup['backup_id'],['memories'],preview['confirmation_token'])
        results['backup_race']='unexpected success'
    except ValueError as exc:
        results['backup_race']=str(exc)
    from saya_app.routers import backups
    fixture_goal=GoalManager(str(base/'restore-kb'))
    fixture_checkin=CheckInManager(str(base/'restore-kb'))
    fixture_goal.add_goal('restore fixture')
    fixture_checkin.save_data()
    restore_manager=BackupManager(base/'restore-kb',base/'restore-chats',base/'restore-zips')
    saved=restore_manager.create_backup(['checkins'])
    preview=restore_manager.preview_restore(saved['backup_id'],['checkins'])
    with patch.object(backups,'backup_manager',restore_manager), patch('core_services.goal_manager',fixture_goal), patch('core_services.checkin_manager',fixture_checkin):
        restored=backups.restore_backup(saved['backup_id'],{'scopes':['checkins'],'confirmation_token':preview['confirmation_token']})
    results['restore_goals']={'status':restored['status'],'runtime_reloaded':restored['runtime_reloaded'],'goals_became_none':fixture_goal.goals is None}

spec=importlib.util.spec_from_file_location('saya_audit_server',ROOT/'server5-22B----.py')
server=importlib.util.module_from_spec(spec)
with patch('sys.stdin',io.StringIO('5\n')):
    spec.loader.exec_module(server)
from saya_app.services.conversation import conversation_service
captured={}
class Response:
    status_code=200; text=''; encoding='utf-8'
    def __enter__(self): return self
    def __exit__(self,*args): return False
    def iter_lines(self,decode_unicode=True):
        yield 'data: '+json.dumps({'choices':[{'delta':{'content':'fixture answer'}}]})
        yield 'data: [DONE]'
def fake_post(*args,**kwargs):
    captured.update(kwargs.get('json') or {})
    return Response()
async def collect(req):
    response=await server.chat_stream(req)
    chunks=[]
    async for chunk in response.body_iterator: chunks.append(str(chunk))
    return ''.join(chunks)
with tempfile.TemporaryDirectory(dir=ROOT/'.audit-20260927') as temp:
    manager=MemoryManager(str(Path(temp)/'chats'))
    kb=KnowledgeBase(str(Path(temp)/'kb'))
    session=manager.create_session()
    with patch.object(server,'memory_manager',manager), patch.object(server,'kb_system',kb), patch.object(server,'journal_service_prompt_context',return_value='PRIVATE_JOURNAL_SENTINEL'), patch.object(server.project_manager,'get_project',return_value={'name':'fixture'}), patch.object(server.project_manager,'build_project_context',return_value='PRIVATE_PROJECT_SENTINEL'), patch.object(conversation_service,'save_credentials',return_value=None), patch.object(server.requests,'post',fake_post):
        output=asyncio.run(collect(server.ChatWithMotion(user_input='hello',model='api',api_key='fixture',api_url='https://example.invalid/v1',api_model_name='fixture-model',session_id=session,temporary_chat=True,context={'project_id':'fixture','checkin_context':'PRIVATE_CHECKIN_SENTINEL'})))
    payload=json.dumps(captured,ensure_ascii=False)
    results['temporary_chat']={name: name in payload for name in ['PRIVATE_JOURNAL_SENTINEL','PRIVATE_PROJECT_SENTINEL','PRIVATE_CHECKIN_SENTINEL']}
    results['temporary_chat']['fixture_answer_streamed']='fixture answer' in output
    results['temporary_chat']['saved_messages']=len(manager.load_session_full(session,make_current=False))
(ROOT/'.audit-20260927/probe-results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(results,ensure_ascii=False,indent=2))
