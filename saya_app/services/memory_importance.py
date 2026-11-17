"""Versioned importance grades and a nonpersistent, host-scoped QQ view."""
from copy import deepcopy
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re

from .memory_origin import current_origin
from .memory_versions import record_version, MemoryVersionConflict
from .state_storage import atomic_save_json

VERSION=1
GRADES=('ordinary','attention','important','critical')
THRESHOLDS=(.45,.70,.85)
MARGIN=.03
FACTOR=1.08
FIELDS=('importance_grade','importance_policy_version','importance_locked','importance_audit')


def number(value):
    value=float(value)
    if not math.isfinite(value) or not 0<=value<=1:raise ValueError('invalid_importance_score')
    return round(value,6)


def grade(score, previous=None):
    score=number(score)
    if previous not in GRADES:return GRADES[sum(score>=x for x in THRESHOLDS)]
    index=GRADES.index(previous)
    while index<3 and score>=round(THRESHOLDS[index]+MARGIN,6):index+=1
    while index>0 and score<round(THRESHOLDS[index-1]-MARGIN,6):index-=1
    return GRADES[index]


def projection(data):
    return {k:deepcopy(data[k]) for k in FIELDS if k in (data or {})}


def view(record, *, score=None):
    score=number((record.get('importance_score') if record.get('importance_score') is not None else .4) if score is None else score)
    initialized=record.get('importance_policy_version')==VERSION and record.get('importance_grade') in GRADES
    return {'importance_grade':record['importance_grade'] if initialized else grade(score),
        'importance_grade_initialized':initialized,'importance_policy_version':VERSION,
        'importance_locked':bool(record.get('importance_locked') or record.get('is_locked')),
        'importance_score':score,'importance_audit':deepcopy(record.get('importance_audit') or [])[-20:]}


def update(data, *, previous=None, score_before=None, score_after=None, reason, manual=False):
    old=previous or {}
    before=number(score_before if score_before is not None else score_after)
    after=number(score_after)
    locked=bool(old.get('importance_locked') or old.get('is_locked'))
    if locked and not manual:after=before;reason='locked_preserved'
    previous_grade=old.get('importance_grade') if old.get('importance_policy_version')==VERSION else grade(before)
    new_grade=grade(after,previous_grade) if previous else grade(after)
    audit=deepcopy(old.get('importance_audit') or [])[-19:]
    origin=(current_origin() or {}).get('origin') or {}
    audit.append({'score_before':before,'score_after':after,'grade_before':previous_grade if previous else None,
        'grade_after':new_grade,'rule_version':VERSION,'reason':reason,
        'evidence_ref':origin.get('message_id'),'observed_at':datetime.now(timezone.utc).isoformat(timespec='seconds')})
    for k in FIELDS:data.pop(k,None)
    data.update(importance_grade=new_grade,importance_policy_version=VERSION,
        importance_locked=bool(old.get('importance_locked')),importance_audit=audit)
    return after


def policy(kb):
    path=Path(kb.base_dir)/'memory_importance_policy.json'
    default={'schema_version':1,'enabled':False,'multiplier':FACTOR}
    if not path.exists():return default
    raw=json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(raw,dict) or raw.get('schema_version')!=1 or type(raw.get('enabled')) is not bool or raw.get('multiplier')!=FACTOR:
        raise ValueError('invalid_importance_policy')
    return raw


def policy_view(kb):
    raw=policy(kb)
    return {**raw,'content_version':record_version(raw),'grade_thresholds':list(THRESHOLDS),'hysteresis':MARGIN,
        'max_qq_score_contribution':.008,'length_adjustment':0}


def set_policy(kb, enabled, expected_version):
    if type(enabled) is not bool:raise ValueError('invalid_importance_policy')
    with kb._memory_write_lock:
        raw=policy(kb)
        if expected_version!=record_version(raw):raise MemoryVersionConflict('importance_policy_changed')
        raw={**raw,'enabled':enabled}
        atomic_save_json(Path(kb.base_dir)/'memory_importance_policy.json',raw)
        return policy_view(kb)


def qq_enabled(kb):
    origin=(current_origin() or {}).get('origin') or {}
    if origin.get('channel')!='qq' or origin.get('verification')!='verified':return False
    try:return policy(kb)['enabled']
    except (ValueError,OSError):return False


def contribution(origin, score):
    origin=origin or {}
    verified=(origin.get('origin_version')==1 and origin.get('channel')=='qq' and origin.get('verification')=='verified'
        and all(re.fullmatch(r'[1-9]\d{0,19}',str(origin.get(k) or '')) for k in ('bot_id','verified_subject'))
        and bool(origin.get('message_id')))
    return round(.10*(min(1,number(score)*FACTOR)-number(score)),6) if verified else 0.0


def preview(kb):
    with kb._memory_write_lock:
        items=[]
        for m in kb.memories:
            state=view(m)
            items.append({'id':m['id'],'title':str(m.get('title') or '')[:120],'expected_version':record_version(m),'score_before':state['importance_score'],
                'score_after':state['importance_score'],'grade':state['importance_grade'],
                'initialized':state['importance_grade_initialized'],'locked':state['importance_locked']})
        return {'items':items,'policy':policy_view(kb),'writes':0,'scores_recomputed':False}


def projections_match(kb, record):
    if kb.sqlite_store and kb.sqlite_store.available:
        with kb.sqlite_store._connection() as db:
            row=db.execute('select payload from memories where id=? and is_deleted=0',(record['id'],)).fetchone()
        indexed=json.loads(row[0]) if row else {}
        if projection(indexed)!=projection(record) or indexed.get('importance_score')!=record.get('importance_score'):return False
    if kb.settings.get('memory_v4_write_enabled'):
        item=kb.memory_v4_store.get_memory(record['id']) if kb.memory_v4_store else None
        if not item or projection(item.metadata)!=projection(record) or item.salience!=record['importance_score']:return False
    return True
