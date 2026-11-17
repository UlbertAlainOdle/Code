"""Versioned local browser preferences, with explicit legacy-cache imports."""
import datetime
import json
from pathlib import Path
import threading

from .credentials_storage import credentials_revision,CredentialsConflict
from .state_storage import atomic_save_json

ARCHIVE_LOCK=threading.RLock()
INVENTORY_PATH=Path(__file__).resolve().parents[2]/'static/browser_settings_inventory.json'


def inventory():return json.loads(INVENTORY_PATH.read_text('utf-8'))['entries']


class BrowserSettingsArchive:
    def __init__(self,path):self.path=Path(path)

    def load(self):
        if not self.path.exists():return {'schema_version':1,'values':{},'history':[]}
        data=json.loads(self.path.read_text('utf-8'))
        self.validate_document(data)
        return data

    def validate_document(self,data):
        if not isinstance(data,dict) or data.get('schema_version')!=1 or not isinstance(data.get('values'),dict) or not isinstance(data.get('history'),list):
            raise ValueError('invalid archive')
        self.validate(data['values'])
        if type(data.get('runtime_enabled',False)) is not bool:raise ValueError('invalid runtime policy')

    def validate(self,values):
        entries=inventory()
        if not isinstance(values,dict) or set(values)-set(entries):raise ValueError('unknown settings')
        if sum(len(v.encode('utf-8')) if isinstance(v,str) else 0 for v in values.values())>8*1024*1024:raise ValueError('archive too large')
        def check_depth(value,depth=0):
            if depth>32:raise ValueError('nested settings')
            if isinstance(value,dict):
                for v in value.values():check_depth(v,depth+1)
            elif isinstance(value,list):
                for v in value:check_depth(v,depth+1)
        for key,raw in values.items():
            if not isinstance(raw,str) or len(raw.encode('utf-8'))>4*1024*1024 or '\x00' in raw:raise ValueError('invalid raw value')
            if entries[key]['kind']=='json':
                value=json.loads(raw,parse_constant=lambda _:(_ for _ in ()).throw(ValueError('nonfinite value')))
                if not isinstance(value,(dict,list)):raise ValueError('invalid settings container')
                check_depth(value)
            elif len(raw)>4096:raise ValueError('text too long')

    def status(self):
        with ARCHIVE_LOCK:
            data=self.load();entries=inventory();categories={}
            for key in data['values']:
                category=entries[key]['category'];categories[category]=categories.get(category,0)+1
            return {'revision':credentials_revision(data),'saved_count':len(data['values']),'categories':categories,
                    'saved_at':data.get('saved_at'),'source_label':data.get('source_label',''),
                    'history_count':len(data['history']),'runtime_applied':data.get('runtime_enabled',False),'storage':'knowledge_base/browser_settings.json'}

    def runtime(self):
        with ARCHIVE_LOCK:
            data=self.load()
            return {**self.status(),'values':data['values'] if data.get('runtime_enabled',False) else {},'entries':inventory()}

    def update_runtime(self,revision,values=None,clear_fields=None,enabled=None):
        values={} if values is None else values
        clear_fields=[] if clear_fields is None else clear_fields
        self.validate(values)
        if not isinstance(clear_fields,list) or any(not isinstance(k,str) or k not in inventory() for k in clear_fields):
            raise ValueError('invalid clear fields')
        if set(values)&set(clear_fields):raise ValueError('ambiguous change')
        if enabled is not None and type(enabled) is not bool:raise ValueError('invalid runtime policy')
        # These are projections of their existing server files, never a second authority.
        server_keys={k for k,v in inventory().items() if v.get('authority')=='existing_server'}
        if (set(values)|set(clear_fields))&server_keys:raise ValueError('server-owned setting')
        with ARCHIVE_LOCK:
            data=self.load()
            if revision!=credentials_revision(data):raise CredentialsConflict('本机设置已更新；当前草稿保留，请重新读取并核对，未覆盖新版本。')
            if enabled is None and not data.get('runtime_enabled',False):
                raise CredentialsConflict('本地统一配置未开启，请刷新页面核对来源。')
            if enabled is not None and (values or clear_fields):raise ValueError('policy and edits must be separate')
            if enabled is True and not data['values']:raise ValueError('capture correct browser first')
            merged=data['values']|values
            for key in clear_fields:merged.pop(key,None)
            policy=data.get('runtime_enabled',False) if enabled is None else enabled
            if merged==data['values'] and policy==data.get('runtime_enabled',False):return self.runtime()
            history=(data['history']+[{'saved_at':data.get('saved_at'),'source_label':data.get('source_label',''),
                'values':data['values'],'runtime_enabled':data.get('runtime_enabled',False)}])[-3:]
            result={**data,'values':merged,'history':history,'runtime_enabled':policy,
                    'saved_at':datetime.datetime.now(datetime.timezone.utc).isoformat()}
            self.path.parent.mkdir(parents=True,exist_ok=True);atomic_save_json(self.path,result)
            return self.runtime()

    def preview(self,values,revision):
        self.validate(values)
        with ARCHIVE_LOCK:
            data=self.load()
            if revision!=credentials_revision(data):raise CredentialsConflict('本机设置存档已更新，请重新预览；浏览器缓存未改变。')
            entries=inventory()
            return {'revision':revision,'changes':[{'key':k,'label':entries[k]['label'],'category':entries[k]['category'],
                     'action':'add' if k not in data['values'] else 'same' if data['values'][k]==v else 'conflict'} for k,v in values.items()],
                    'retained_count':len(set(data['values'])-set(values))}

    def save(self,values,revision,overwrite=False,source_label=''):
        if type(overwrite) is not bool or not isinstance(source_label,str) or len(source_label)>96 or any(c in source_label for c in '\r\n\x00'):
            raise ValueError('invalid save mode')
        if not values:raise ValueError('empty capture')
        with ARCHIVE_LOCK:
            preview=self.preview(values,revision)
            if not overwrite and any(c['action']=='conflict' for c in preview['changes']):
                raise CredentialsConflict('存在不同的已存设置；请核对当前浏览器后明确允许覆盖，再保存。')
            data=self.load();history=list(data['history'])
            if data['values']:
                history=(history+[{'saved_at':data.get('saved_at'),'source_label':data.get('source_label',''),'values':data['values']}])[-3:]
            result={**data,'schema_version':1,'values':data['values']|values,'history':history,
                    'saved_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'source_label':source_label.strip()}
            self.path.parent.mkdir(parents=True,exist_ok=True);atomic_save_json(self.path,result)
            return self.status()
