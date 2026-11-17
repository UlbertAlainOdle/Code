"""Offline frozen-source A/B: event selection contracts, never model/QQ effects."""
import argparse
import asyncio
from datetime import datetime
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'tests'))
from saya_app.services.qq_rollback import NETWORK_GUARD
exec(NETWORK_GUARD)
from saya_app.services.qq_bridge import normalize_event
from saya_app.services.qq_episode_context import QQEpisodeContext
from test_qq_task_scheduler import make
from test_qq_delivery import event


def frozen_class(root,name,class_name):
    path=root/'saya_app/services'/name
    spec=importlib.util.spec_from_file_location('saya_app.services._priority_baseline_'+path.stem,path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return getattr(module,class_name)


async def evaluate(work,baseline,corpus):
    prior_episode=frozen_class(baseline,'qq_episode_context.py','QQEpisodeContext')
    prior_people=frozen_class(baseline,'qq_person_events.py','QQPersonEvents')
    base=datetime.fromisoformat(corpus['clock']).timestamp();results=[]
    for case in corpus['cases']:
        outcome={'id':case['id'],'positive':case.get('positive',False)}
        for side in ['A','B']:
            bridge=make(work/case['id']/side,None);await bridge.start();now=[base]
            bridge.context_salience.clock=lambda:now[0]
            s=(prior_episode if side=='A' else QQEpisodeContext)(bridge,clock=lambda:now[0])
            if side=='A':s.people=prior_people(s)
            bridge.episodes=s
            bridge.context_salience.configure('20000',0,{'enabled':True,'timezone':corpus['timezone']})
            s.configure('20000',0,{'enabled':True});s.people.migrate('20000',s.people.migration_preview()['version'])
            async def decide(*args,**kwargs):return {'action':'create','confidence':.99}
            s.decide=decide;sequence=0;ids=[]
            def message(text,stamp):
                nonlocal sequence
                sequence+=1;raw=event(text,sequence);raw['time']=int(stamp)
                msg,error=normalize_event(raw,bridge.settings,now=stamp)
                assert msg is not None,error
                msg.context_snapshot=bridge.context_salience.snapshot(msg.scope+':shared')
                return msg
            try:
                for item in case['cards']:
                    now[0]=base-item['age'];msg=message(item['text'],now[0]);before={c['id'] for c in s.people._canonical(msg.user_id)}
                    await s.observe(msg,[]);created=[c for c in s.people._canonical(msg.user_id) if c['id'] not in before]
                    assert len(created)==1,'preparation_failed:'+case['id']
                    identifier=created[0]['id'];ids.append(identifier)
                    if item.get('manual'):s.command(message('/events importance '+identifier+' '+item['manual'],now[0]))
                    if item.get('stop'):s.command(message('/events stop '+identifier,now[0]))
                now[0]=base;query=message('松湖资料',base);snapshot=s.context(query)
                selected=snapshot['cards'][0]['event_id'] if snapshot else None
                passed=selected==ids[case['target']]
                if side=='B' and case.get('unknown'):
                    unknown_card=next(c for c in s.people._canonical(query.user_id) if c['id']==ids[0])
                    passed=passed and unknown_card.get('timing',{}).get('deadline',{}).get('status')=='unknown'
                if side=='B' and case.get('date_only'):
                    passed=passed and 'T' not in snapshot['cards'][0]['timing']['deadline']['local']
                outcome[side]={'correct':bool(passed),'selected_index':ids.index(selected) if selected in ids else None,
                    'tokens':snapshot['tokens'] if snapshot else 0}
            finally:await bridge.stop()
        results.append(outcome)
    positive=[x for x in results if x['positive']];constraints=[x for x in results if not x['positive']]
    score={side:sum(x[side]['correct'] for x in positive) for side in ['A','B']}
    regressions=sum(x['A']['correct'] and not x['B']['correct'] for x in results)
    failures=sum(not x['B']['correct'] for x in constraints)
    gate=score['B']>=corpus['protocol']['positive_minimum'] and score['B']-score['A']>=corpus['protocol']['minimum_improvement'] and not regressions and not failures
    report={'gate':'PASS' if gate else 'FAIL','positive':score,'positive_total':len(positive),
        'constraints_passed':len(constraints)-failures,'constraints_total':len(constraints),'regressions':regressions,
        'model_calls':0,'QQ_sends':0,'production_writes':0,'cases':results,
        'scope':'Frozen prior event observer/selector versus current; shared unchanged authentication/control fixture. Deterministic ranking only, not live model benefit.'}
    (work/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k!='cases'},ensure_ascii=False))
    if not gate:raise SystemExit(1)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--baseline-source',required=True);parser.add_argument('--output',required=True)
    args=parser.parse_args();private=(ROOT/'.runtime/qq-upgrade').resolve();out=Path(args.output).resolve();prior=Path(args.baseline_source).resolve()
    assert out.is_relative_to(private) and prior.is_relative_to(private),'private_evidence_required'
    out.mkdir(parents=True,exist_ok=False);corpus_path=ROOT/'tests/data/qq_event_priority_eval_v1.json';corpus=json.loads(corpus_path.read_text(encoding='utf-8'))
    (out/'protocol.json').write_text(json.dumps({'corpus':corpus,'sha256':hashlib.sha256(corpus_path.read_bytes()).hexdigest(),
        'baseline_hashes':{p:hashlib.sha256((prior/p).read_bytes()).hexdigest() for p in ['saya_app/services/qq_episode_context.py','saya_app/services/qq_person_events.py']}},ensure_ascii=False,indent=2),encoding='utf-8')
    asyncio.run(evaluate(out,prior,corpus))
