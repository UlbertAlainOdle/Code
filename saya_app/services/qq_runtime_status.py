"""Local diagnostic projection: no process IDs, paths, keys or conversations."""

import json
from pathlib import Path
import time

from .qq_lifecycle import matches_identity
from .qq_napcat import NapcatSupervisor
from .qq_observation import ObservationLedger

PHASES={'disabled','manual_stop','installation_invalid','ownership_unknown','foreign_profile','multiple_instances',
        'running_owned','exit_grace','root_reappeared','cleaning_owned_children','startup_timeout','waiting_launcher',
        'blocked_foreign_port','retry_exhausted','backoff','starting','launch_failed'}


def runtime_status(root, directory=None, now=None):
    root=Path(root); directory=Path(directory) if directory is not None else root/'.runtime/qq-service'
    now=time.time() if now is None else now
    def read(name):
        try:
            value=json.loads((directory/name).read_text(encoding='utf-8'))
            return value if isinstance(value,dict) else {}
        except (OSError,ValueError): return {}
    owner=read('supervisor-owner.json')
    active=matches_identity(owner,root,root/'scripts/manage_qq_service.py')
    napcat=NapcatSupervisor(root,directory)
    try: policy=napcat.policy.load()
    except (OSError,ValueError): policy={'enabled':False,'revision':None,'invalid':True}
    state=read('napcat-status.json')
    phase=state.get('phase') if isinstance(state.get('phase'),str) and state.get('phase') in PHASES else 'unknown'
    try: stale=not state or now-float(state.get('updated',0))>15
    except (ValueError,TypeError): stale=True
    try: observation=ObservationLedger(directory/'observation.json').summary(now)
    except (OSError,ValueError,TypeError): observation={'stale':True,'gate':'invalid'}
    return {'backend_watchdog':'running' if active else 'stopped',
            'napcat':{'policy':policy,'phase':phase,'stale':stale,'supervisor_active':active,
                      'failures':state.get('failures',0) if type(state.get('failures',0)) is int else 0},
            'observation':observation}
