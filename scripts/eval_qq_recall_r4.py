"""Fixed synthetic R4 information-set baseline; never uses the live KB or model."""
from dataclasses import dataclass,asdict
import hashlib,json
from pathlib import Path
import tempfile


@dataclass(frozen=True)
class Case:
    name:str
    question:str
    required:tuple=()
    forbidden:tuple=()
    subject:str|None=None
    reason_state:str|None=None
    scope_id:str|None=None
    max_items:int=5


CASES=(
    Case('reason_exact','我为什么为青岚计划选择离线处理？',('choice','reason'),subject='user',reason_state='explicit_passages'),
    Case('reason_paraphrase','青岚计划当初选离线方案的原因是什么？',('choice','reason'),reason_state='explicit_passages'),
    Case('reason_cross_day','之前我在青岚计划为什么选择离线处理？',('choice','reason'),subject='user',reason_state='explicit_passages'),
    Case('reason_small_set','我在青岚计划选择离线处理的理由是什么？',('choice','reason'),max_items=2),
    Case('no_recorded_reason','我为什么给棠木计划选木结构？',('no_reason',),subject='user',reason_state='not_found_in_selected_material'),
    Case('other_person','林舟为什么在青岚计划选择联网处理？',('other_person',),subject='person:lin'),
    Case('current_version','松涧计划我现在使用什么配色？',('current',),('superseded',)),
    Case('past_still_allowed','我之前在青岚计划采用的处理方案是什么？',('choice',)),
    Case('future_explicit','我明年的云堤计划要选什么接口？',('future',)),
    Case('unrelated_unknown','我说过紫钙星穹协议的细节吗？'),
    Case('pending_not_fact','我在风栈计划的固定方案是什么？',(),('candidate',)),
    Case('deleted_not_recalled','我对赤枫计划的安排是什么？',(),('deleted',)),
    Case('blocked_not_recalled','我之前对沉帆计划有什么安排？',(),('blocked',)),
    Case('expired_not_recalled','我以前对红陶计划作过什么安排？',(),('expired',)),
    Case('scope_alpha','我在项目青一的接口选型是什么？',('project_alpha',),('project_beta',),scope_id='alpha'),
    Case('scope_global','我在项目青一的接口选型是什么？',(),('project_alpha','project_beta')),
)


def seed(root):
    from core_services import KnowledgeBase
    from saya_app.services.memory_v4 import MemoryItemV4,MemoryEvidence
    kb=KnowledgeBase(str(Path(root)/'knowledge_base'))
    kb.update_settings({'memory_v4_read_enabled':True,'memory_v4_write_enabled':True,
                        'embedding_enabled':False,'memory_core_limit':0})
    ids={}
    def add(label,text,**kwargs):
        item=kb.add_memory(text,title=label,category='preference',metadata={'id':'r4:'+label,'memory_layer':'semantic'},**kwargs)
        ids[label]=item['id'];return item
    add('choice','虚构青岚计划：我选择离线处理方案。')
    add('reason','虚构青岚计划选择离线处理的理由：海上作业经常断网。')
    add('distractor','虚构银湾计划：我选择联网处理，因为办公室网络稳定。',is_important=True)
    add('no_reason','虚构棠木计划：我选择木结构；没有记录选择原因。')
    add('deleted','虚构赤枫计划：我准备红色接口。');kb.delete_memory(ids['deleted'])
    add('blocked','虚构沉帆计划：我准备小型接口。');kb.set_memory_use_policy(ids['blocked'],True)
    def native(label,text,**kwargs):
        item=MemoryItemV4(id='r4:'+label,canonical_text=text,subject='user',predicate='choice',
            kind='preference',authority='user_confirmed',created_at='2025-01-01T00:00:00',
            updated_at='2025-01-01T00:00:00',**kwargs)
        kb.memory_v4_store.upsert_memory(item,[MemoryEvidence(id='e:'+label,memory_id=item.id,
            source_type='synthetic_r4_fixture',source_id=label,quote=text)])
        ids[label]=item.id
    # Standalone native records test the actual V4 read path, not a fake scorer.
    item=MemoryItemV4(id='r4:other_person',canonical_text='虚构人物林舟在青岚计划选择联网处理，因为他办公室网络稳定。',
        subject='person:lin',predicate='choice',kind='preference',authority='observed')
    kb.memory_v4_store.upsert_memory(item,[MemoryEvidence(id='e:other',memory_id=item.id,
        source_type='synthetic_r4_fixture',source_id='other',quote=item.canonical_text)])
    ids['other_person']=item.id
    native('superseded','虚构松涧计划：我以前用红色配色。',lifecycle='superseded')
    native('current','虚构松涧计划：我现在用深蓝配色。',supersedes_id=ids['superseded'])
    native('candidate','虚构风栈计划：我固定用银色接口。',lifecycle='candidate')
    native('expired','虚构红陶计划：我固定用小型接口。',valid_to='2000-01-01')
    native('future','虚构云堤计划明年使用云接口。',valid_from='2027-01-01')
    native('project_alpha','虚构项目青一：我选择木制接口。',scope_type='project',scope_id='alpha')
    native('project_beta','虚构项目青一：我选择金属接口。',scope_type='project',scope_id='beta')
    return kb,ids


def evaluate():
    rows=[]
    with tempfile.TemporaryDirectory(prefix='saya-r4-recall-') as directory:
        kb,ids=seed(directory);labels={value:key for key,value in ids.items()}
        for case in CASES:
            registry=kb._memory_v4_capability_registry(allow_legacy_memory=True)
            args={'query':case.question,'max_items':case.max_items,'max_candidates':40,'token_budget':1200,
                  'scope_type':'project' if case.scope_id else 'global'}
            if case.scope_id:args['scope_id']=case.scope_id
            result=registry.get('memory.query').handler(args)
            selected=result.get('items',[]);found={x['id'] for x in selected}
            required={ids[key] for key in case.required};forbidden={ids[key] for key in case.forbidden}
            evidence=result.get('evidence_contract',{})
            checks={'necessary_set_complete':required<=found,'forbidden_absent':not bool(found&forbidden),
                'source_metadata_preserved':all(all(field in item for field in ['subject','authority','lifecycle','revision','valid_from','valid_to']) for item in selected),
                'no_match_is_not_never_recorded':bool(selected) or evidence.get('retrieval_status')=='no_match_in_current_read'}
            if case.reason_state:checks['reason_boundary']=evidence.get('reason_status')==case.reason_state
            if case.subject and selected:checks['subject_metadata_present']=any(item.get('subject')==case.subject for item in selected)
            rows.append({'name':case.name,'required':list(case.required),'retrieved':[labels.get(x['id'],'unknown') for x in selected],
                'missing':[labels[x] for x in sorted(required-found)],'forbidden_found':[labels[x] for x in sorted(found&forbidden)],
                'checks':checks,'passed':all(checks.values())})
    corpus_hash=hashlib.sha256(json.dumps([asdict(c) for c in CASES],sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    return {'kind':'synthetic_host_retrieval_baseline','cases':len(rows),'passed':sum(row['passed'] for row in rows),
        'set_complete':sum(row['checks']['necessary_set_complete'] for row in rows),
        'forbidden_absent':sum(row['checks']['forbidden_absent'] for row in rows),
        'metadata_complete':sum(row['checks']['source_metadata_preserved'] for row in rows),
        'corpus_sha256':corpus_hash,'cases_detail':rows,'real_QQ_acceptance':False,'real_model_answer_quality':False,
        'paid_provider_calls':0,'live_KB_writes':0}


if __name__=='__main__':
    import argparse,sys
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
    from saya_app.services.qq_rollback import NETWORK_GUARD
    exec(NETWORK_GUARD,{})
    parser=argparse.ArgumentParser();parser.add_argument('--output',required=True);args=parser.parse_args()
    result=evaluate()
    with Path(args.output).open('x',encoding='utf-8') as out:json.dump(result,out,ensure_ascii=False,indent=2)
    print(json.dumps({key:value for key,value in result.items() if key!='cases_detail'},ensure_ascii=False))
