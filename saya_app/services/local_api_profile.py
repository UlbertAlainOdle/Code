"""One local API profile, explicit cache transfers, and optimistic concurrency."""

from .credentials_storage import CREDENTIALS_LOCK, CredentialsConflict, credentials_revision, read_credentials, update_credentials
from .web_research.providers import official_deepseek_url

API_FIELDS = ('api_key','api_url','api_model_name','multimodal_model_name','strong_logic_model_name',
              'autonomous_memory_model_name','memory_strong_model_name','brave_search_api_key','deepseek_search_api_key')
OTHER_FIELDS = ('translation_secret_id','translation_secret_key','translation_region','translation_endpoint',
                'tts_api_url','tts_api_key','tts_voice_id','tts_model_id')
SECRET_FIELDS = {'api_key','brave_search_api_key','deepseek_search_api_key','translation_secret_id','translation_secret_key','tts_api_key'}
ALL_FIELDS = set(API_FIELDS + OTHER_FIELDS)


class LocalApiProfile:
    def __init__(self, path):
        self.path = path

    def read(self):
        with CREDENTIALS_LOCK:
            data = read_credentials(self.path)
            return {key:data[key] for key in ALL_FIELDS if key in data and isinstance(data[key],str)}

    def status(self):
        with CREDENTIALS_LOCK:
            data = read_credentials(self.path)
            return {'revision':credentials_revision(data), 'prefer_local':data.get('_prefer_local',True) is True,
                    'search_provider':data.get('search_provider','brave'),
                    'deepseek_model_key_available':bool(data.get('api_key')) and official_deepseek_url(data.get('api_url')),
                    'managed':data.get('_local_profile_managed',False) is True,
                    'configured':{key:bool(data.get(key)) for key in API_FIELDS + OTHER_FIELDS},
                    'values':{key:data[key] for key in ALL_FIELDS - SECRET_FIELDS if isinstance(data.get(key),str)},
                    'present_fields':[key for key in API_FIELDS + OTHER_FIELDS if key in data],
                    'storage':'knowledge_base/api_credentials.json'}

    def runtime(self, revision):
        with CREDENTIALS_LOCK:
            data = read_credentials(self.path)
            if revision != credentials_revision(data):
                raise CredentialsConflict('本机配置已更新，请重新读取。')
            return self.status() | {'settings':self.read()}

    def save(self, values, revision, overwrite=False, clear_fields=None, prefer_local=True):
        if not isinstance(values,dict) or set(values)-ALL_FIELDS:
            raise ValueError('unsupported profile fields')
        if type(overwrite) is not bool or type(prefer_local) is not bool:
            raise ValueError('invalid mode')
        clear_fields = [] if clear_fields is None else clear_fields
        if not isinstance(clear_fields,list) or any(not isinstance(k,str) or k not in ALL_FIELDS for k in clear_fields):
            raise ValueError('invalid clear fields')
        clean = {}
        for key,value in values.items():
            if not isinstance(value,str) or len(value)>8192 or any(c in value for c in '\r\n\x00'):
                raise ValueError('invalid profile value')
            value = value.strip()
            if key in {'brave_search_api_key','deepseek_search_api_key'} and len(value)>512:
                raise ValueError('invalid search key')
            if value and key in {'api_url','tts_api_url'}:
                from .qq_bridge import QQSettings
                QQSettings.valid_url(value)
            if value: clean[key] = value
        if set(clean) & set(clear_fields):
            raise ValueError('cannot set and clear the same field')
        with CREDENTIALS_LOCK:
            data = read_credentials(self.path)
            if revision != credentials_revision(data):
                raise CredentialsConflict('本机配置已更新，请重新读取后再保存；浏览器草稿已保留。')
            collisions = [k for k,v in clean.items() if data.get(k) and data[k] != v]
            if collisions and not overwrite:
                raise CredentialsConflict('存在不同的已存字段：'+ '、'.join(sorted(collisions))+'。默认不会覆盖；确认后勾选“允许覆盖同名字段”。')
            # A partially supplied model bundle cannot pair a new key with an
            # unrelated old endpoint. Explicit clears remove the whole bundle.
            model_fields = {'api_key','api_url','api_model_name'}
            if model_fields & set(clean) and not model_fields <= set(clean):
                raise ValueError('模型连接须同时填写 Base URL、API Key 和模型，避免混用。')
            if model_fields & set(clear_fields): clear_fields = list(set(clear_fields) | model_fields)
            updates = clean | {key:'' for key in clear_fields}
            updates.update(_local_profile_managed=True,_prefer_local=prefer_local)
            update_credentials(self.path, updates, expected_revision=revision)
            return self.status()

    def select_search_provider(self,provider,revision,api_key=None,reuse_model_key=False,overwrite=False,effective_credentials=None):
        from .web_research.providers import PROVIDERS,official_deepseek_url
        if provider not in PROVIDERS or type(reuse_model_key) is not bool or type(overwrite) is not bool:
            raise ValueError('invalid provider')
        if api_key is not None and (not isinstance(api_key,str) or len(api_key)>512 or any(c in api_key for c in '\r\n\x00')):
            raise ValueError('invalid search key')
        if reuse_model_key and (provider!='deepseek' or (api_key and api_key.strip())):raise ValueError('ambiguous key source')
        with CREDENTIALS_LOCK:
            stored=read_credentials(self.path)
            if credentials_revision(stored)!=revision:raise CredentialsConflict('本机配置已更新；请重新读取版本，未切换搜索服务，草稿保留。')
            field=provider+'_search_api_key'
            updates={};supplied=(api_key or '').strip()
            effective=effective_credentials or {}
            if reuse_model_key:
                if not official_deepseek_url(effective.get('api_url')) or not effective.get('api_key'):
                    raise ValueError('仅可复制本机已配置的DeepSeek官方模型密钥；中转服务密钥不可复用。')
                supplied=effective['api_key']
                if len(supplied)>512:raise ValueError('invalid search key')
            if supplied:
                if stored.get(field) and stored[field]!=supplied and not overwrite:
                    raise CredentialsConflict('该厂家已有不同密钥；请先勾选本机配置面板的“允许覆盖同名字段”，再启用。')
                updates[field]=supplied
            if not (supplied or stored.get(field) or effective.get(field)):
                raise ValueError('请先填写该厂家的搜索密钥，或选择复制本机DeepSeek官方模型密钥。')
            updates.update(search_provider=provider,_local_profile_managed=True)
            update_credentials(self.path,updates,expected_revision=revision)
            return self.status()
