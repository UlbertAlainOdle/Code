"""Opt-in real-model fictional A/B, isolated history; never sends QQ messages.

Run baseline before editing, candidate with --variant candidate afterwards.
Outputs stay under .runtime; it does not enable runtime policies or change keys.
"""
import argparse,asyncio,hashlib,importlib.util,io,json,statistics,sys,time
from datetime import datetime,timezone,timedelta
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def canonical_history(lines):
    return [('Doctor:'+line[5:] if line.startswith('User:') else 'Saya:'+line[3:] if line.startswith('AI:') else line) for line in lines]


def fictional_clock(group,index):
    hour=9 if (group,index) in {('casual',0),('voice',6)} else 22 if (group,index) in {('casual',6),('voice',1),('noextras',6)} else 18
    return datetime(2026,11,3,hour,0,tzinfo=timezone(timedelta(hours=8))).timestamp()


async def run(args):
    from core_services import KnowledgeBase,MemoryManager
    from saya_app.services.qq_bridge import QQSettings,normalize_event
    from saya_app.services.qq_chat import QQChatResponder
    from saya_app.services.conversation import conversation_service
    directory=ROOT/'.runtime/qq-upgrade'/args.run
    directory.mkdir(exist_ok=False)
    spec=importlib.util.spec_from_file_location('qq_experience_lab',ROOT/'server5-22B----.py')
    server=importlib.util.module_from_spec(spec)
    with patch('sys.stdin',io.StringIO('5\n')),patch.dict('os.environ',{'SAYA_STARTUP_MODEL':'api'}):
        spec.loader.exec_module(server)
    clock_value=fictional_clock('casual',0)
    real_clock=server.clock_context
    server.clock_context=lambda **kw:real_clock(**(kw|{'now':clock_value}))
    class FictionalDateTime(datetime):
        @classmethod
        def now(cls,tz=None):
            value=datetime.fromtimestamp(clock_value,tz or timezone(timedelta(hours=8)))
            return value if tz else value.replace(tzinfo=None)
    server.datetime=FictionalDateTime
    creds=server._qq_credentials()
    assert creds['configured'],'model_not_configured'
    server.kb_system=KnowledgeBase(str(directory/'kb'))
    server.memory_manager=MemoryManager(str(directory/'chats'))
    server.memory_manager.legacy_sessions_file=str(directory/'no_legacy.json')
    settings=QQSettings(enabled=True,task_control_enabled=True,access_token='fixture',webhook_token='fixture',bot_id='10000',
        owner_ids={'20000'},allowed_users={'20000'},shared_memory=False,agent_mode=False,recall_mode='off',web_search_mode='off',
        model='api',thinking_effort='auto',max_output_tokens=8192)
    async def single(req,*,channel_turn):
        response=await server._chat_stream_single(req,channel_turn=channel_turn)
        iterator=response.body_iterator
        async def observe():
            try:
                async for chunk in iterator:
                    if isinstance(chunk,str) and any(k in chunk for k in ('phase": "failed','event: stream_error','API Error:','System Error:')):
                        with (directory/'provider-failures.txt').open('a',encoding='utf8') as out:out.write(chunk+'\n')
                    yield chunk
            finally:await iterator.aclose()
        response.body_iterator=observe()
        return response
    responder=QQChatResponder(settings,server.ChatWithMotion,single,lambda:'api',lambda:creds)
    responder.expression_managed=True
    if args.variant=='candidate':
        responder.dialogue_experience=type('Policy',(),{'state':lambda s:{'revision':1,'values':{'enabled':True}}})()
    responder.channel_capabilities=lambda m:{'voice_output':{'supported':True,'available':True,'reason':'available',
        'max_chars':120,'max_seconds':30,'requested_this_turn':False,'compatible_with':['face','kaomoji'],
        'incompatible_with':['sticker']},'expression_mode':'random','voice_input_recognition':False,'ordinary_file_output':False}
    cases=json.loads((ROOT/'scripts/qq_dialogue_experience_cases.json').read_text('utf8'))
    report={'variant':args.variant,'source':{n:hashlib.sha256((ROOT/n).read_bytes()).hexdigest() for n in
        ['saya_app/services/qq_chat.py','saya_app/services/qq_expressions.py','server5-22B----.py']},
        'model_fingerprint':hashlib.sha256((creds['api_url']+'\0'+creds['model']).encode()).hexdigest(),
        'thinking':'auto','max_output_tokens':8192,'lab_adapter_version':2,'clock_fixture':'fixed_per_case_Asia_Shanghai_v1',
        'synthetic_cases':True,'QQ_sends':0,'search_calls':0,'TTS_calls':0,'cases':[]}
    report['persona_fingerprint']=hashlib.sha256((server.api_system_prompt+server.system_prompt).encode()).hexdigest()
    report['profile_style_fingerprints']={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest()
        for p in [Path(server.PROFILE_PATH),ROOT/'knowledge_base/saya_style_settings.json'] if p.is_file() and p.is_relative_to(ROOT)}
    report['cases_fingerprint']=hashlib.sha256((ROOT/'scripts/qq_dialogue_experience_cases.json').read_bytes()).hexdigest()
    consecutive_failures=0
    try:
        with patch.object(conversation_service,'save_credentials',lambda **kw:None),patch.object(server,'refresh_daily_activity_memory',lambda:None):
            for group,items in cases['groups'].items():
                for index,(question,history) in enumerate(items):
                    if args.limit and len(report['cases'])>=args.limit:break
                    history=canonical_history(history)
                    clock_value=fictional_clock(group,index)
                    from saya_app.services.qq_voice import voice_requested
                    responder.channel_capabilities=lambda m:{'voice_output':{'supported':True,'available':True,'reason':'available',
                        'max_chars':120,'max_seconds':30,'requested_this_turn':voice_requested(m.text),
                        'short_segment_supported':args.variant=='candidate'},'expression_mode':'random'}
                    event=dict(time=clock_value,self_id=10000,post_type='message',message_type='private',sub_type='friend',
                        user_id=20000,message_id=len(report['cases'])+1,message=[{'type':'text','data':{'text':question}}])
                    message,reason=normalize_event(event,settings,now=event['time'])
                    assert message is not None,reason
                    began=time.monotonic()
                    item={'id':group+'-'+str(index+1),'group':group,'question':question,'history':history}
                    try:
                        answer=await asyncio.wait_for(responder(message,history,'isolated'),args.timeout)
                        body=str(getattr(answer,'body',answer));error=body.startswith(('API stream interrupted:','API Error:','System Error:','[System Error]'))
                        item.update(text=body,chars=len(body),valid=bool(body) and not error)
                    except Exception as error:
                        import traceback
                        (directory/'failure.txt').write_text(traceback.format_exc(),'utf8')
                        item.update(valid=False,error=type(error).__name__)
                    item['seconds']=round(time.monotonic()-began,3);report['cases'].append(item)
                    (directory/'results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),'utf8')
                    print(json.dumps({k:v for k,v in item.items() if k in {'id','chars','seconds','valid','error'}}),flush=True)
                    consecutive_failures=0 if item['valid'] else consecutive_failures+1
                    if consecutive_failures>=3:break
                if consecutive_failures>=3:break
    finally:
        await server._get_api_httpx_client().aclose()
        report['production_memory_writes']=0
        report['result']='PASS_GENERATION' if len(report['cases'])==(args.limit or 48) and all(x['valid'] for x in report['cases']) else 'INCOMPLETE'
        report['medians']={g:statistics.median([x['chars'] for x in report['cases'] if x['group']==g and x['valid']])
            for g in cases['groups'] if any(x['group']==g and x['valid'] for x in report['cases'])}
        (directory/'results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),'utf8')
        print(json.dumps({'result':report['result'],'cases':len(report['cases']),'medians':report['medians']}),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--variant',choices=['baseline','candidate'],required=True)
    parser.add_argument('--run',required=True)
    parser.add_argument('--limit',type=int,default=0)
    parser.add_argument('--timeout',type=int,default=100)
    args=parser.parse_args()
    if not args.run.startswith('session77-') or '/' in args.run or '\\' in args.run:parser.error('invalid run directory')
    asyncio.run(run(args))
