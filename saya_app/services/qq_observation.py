"""Bounded content-free availability evidence; gaps are never credited as uptime."""

import json
from pathlib import Path
import time

from .state_storage import atomic_save_json

FIELDS={'saya','consumer','napcat','onebot_auth','qq_login','model','search','service_mode','network'}
VALUES={'ready','running','paused','draining','valid','invalid','unknown','reachable','unreachable','timeout',
        'action_failed','accepted','rejected','http_error','login_required','matched','wrong_account',
        'configured_unverified','not_configured','manual_stop','maintenance','stopped',
        'offline','login_initializing','logged_in_unverified'}


class ObservationLedger:
    def __init__(self,path,max_gap=90,max_events=100):
        self.path=Path(path)
        self.max_gap=max_gap
        self.max_events=max_events

    def load(self):
        if not self.path.exists(): return {}
        data=json.loads(self.path.read_text(encoding='utf-8'))
        if not isinstance(data,dict) or data.get('schema_version')!=1: raise ValueError('invalid observation file')
        return data

    def record(self,health,now=None):
        now=time.time() if now is None else now
        data=self.load()
        sample={key:value for key,value in health.items() if key in FIELDS and isinstance(value,str) and value in VALUES}
        healthy=all(sample.get(key)==value for key,value in {'saya':'ready','consumer':'running','napcat':'reachable',
            'onebot_auth':'accepted','qq_login':'matched'}.items()) and sample.get('service_mode','running')=='running'
        # Preserve legacy records. New probes explicitly report connectivity;
        # cached account identity cannot grant uptime during a network outage.
        healthy = healthy and ('network' not in sample or sample['network'] == 'reachable')
        previous=data.get('last_sample')
        if previous is not None and now<float(previous):
            data['clock_regressions']=data.get('clock_regressions',0)+1
            atomic_save_json(self.path,data)
            return data
        if previous is not None:
            elapsed=max(0.,now-float(previous))
            if elapsed>self.max_gap:
                data['unknown_seconds']=data.get('unknown_seconds',0.)+elapsed
                data['gap_count']=data.get('gap_count',0)+1
            else:
                data['measured_seconds']=data.get('measured_seconds',0.)+elapsed
                field='healthy_seconds' if healthy and data.get('last_healthy') else 'unhealthy_seconds'
                data[field]=data.get(field,0.)+elapsed
        for field in ('measured_seconds','healthy_seconds','unhealthy_seconds','unknown_seconds'):
            data.setdefault(field,0.)
        events=data.get('events',[])
        if sample!=data.get('last_state'):
            events=(events+[{'at':now,'state':sample}])[-self.max_events:]
        data.update(schema_version=1,started=data.get('started',now),last_sample=now,last_state=sample,last_healthy=healthy,
            events=events,ready_for_24h_review=data['measured_seconds']>=86400,
            gate='manual_review_required' if data['measured_seconds']>=86400 else 'observing')
        self.path.parent.mkdir(parents=True,exist_ok=True)
        atomic_save_json(self.path,data)
        return data

    def summary(self,now=None):
        now=time.time() if now is None else now
        data=self.load()
        delta=now-float(data['last_sample']) if data else 0.
        stale=not data or delta>self.max_gap or delta<0
        tail=max(0.,delta) if stale and data else 0.
        return {key:data.get(key) for key in ('started','last_sample','measured_seconds','healthy_seconds','unhealthy_seconds',
            'gap_count','ready_for_24h_review','gate')} | {'stale':stale,'unobserved_tail_seconds':tail,
            'unknown_seconds':float(data.get('unknown_seconds',0.))+tail}
