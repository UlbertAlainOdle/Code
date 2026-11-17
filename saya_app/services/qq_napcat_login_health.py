"""Read-only login diagnosis when OneBot is unavailable before QQ login."""
import asyncio
import hashlib
import json
from pathlib import Path

import httpx

from saya_app.config import PROJECT_ROOT
from .qq_napcat import load_install


class NapcatLoginProbe:
    def __init__(self,root,transport=None):
        self.root=Path(root)
        self.transport=transport
        self._credential=''
        self._fingerprint=None
        self._lock=asyncio.Lock()

    async def probe(self):
        try:
            config=load_install(self.root)
            webui=json.loads((config['profile_dir']/'config/webui.json').read_text(encoding='utf-8'))
            token=webui.get('token')
            port=webui.get('port',6099)
            if not isinstance(token,str) or not token or type(port) is not int or not 1<=port<=65535:
                return 'unknown'
            fingerprint=hashlib.sha256((str(port)+'\0'+token).encode()).hexdigest()
            async with self._lock:
                if self._fingerprint!=fingerprint:
                    self._credential='';self._fingerprint=fingerprint
                async with httpx.AsyncClient(transport=self.transport,timeout=1.5,trust_env=False,follow_redirects=False) as client:
                    origin=f'http://127.0.0.1:{port}'
                    if not self._credential:
                        response=await client.post(origin+'/api/auth/login',json={'hash':hashlib.sha256((token+'.napcat').encode()).hexdigest()})
                        response.raise_for_status();payload=response.json()
                        if not isinstance(payload,dict):return 'unknown'
                        auth_data=payload.get('data')
                        credential=auth_data.get('Credential') if payload.get('code')==0 and isinstance(auth_data,dict) else None
                        if not isinstance(credential,str) or not credential:return 'unknown'
                        self._credential=credential
                    response=await client.post(origin+'/api/QQLogin/CheckLoginStatus',json={},headers={'Authorization':'Bearer '+self._credential})
                    if response.status_code in {401,403}:
                        self._credential='';return 'unknown'
                    response.raise_for_status();payload=response.json()
                    if not isinstance(payload,dict):return 'unknown'
                    if payload.get('code')!=0:
                        self._credential='';return 'unknown'
                    data=payload.get('data')
                    if not isinstance(data,dict):return 'unknown'
                    # Never return QR URLs, errors, account IDs, or the auth credential.
                    if data.get('isLogin') is True:return 'logged_in_unverified'
                    if data.get('isOffline') is True:return 'offline'
                    if data.get('qrLoginAccepted') is True or data.get('loginPhase') in {'qrcode_scanned','initializing'}:
                        return 'login_initializing'
                    if data.get('isLogin') is False:return 'login_required'
                    return 'unknown'
        except (OSError,ValueError,TypeError,KeyError,httpx.HTTPError):
            return 'unknown'


napcat_login_probe=NapcatLoginProbe(PROJECT_ROOT)
