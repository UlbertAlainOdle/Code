"""Durable invalidation boundary for contexts derived before a memory edit.

The journal contains versions only, never memory/chat bodies. Historical files
remain intact; prompt projections use current-era entries after a mutation.
"""
from contextlib import contextmanager
from contextvars import ContextVar
import json
import re
from pathlib import Path
import time
from uuid import uuid4

from .state_storage import atomic_save_json

_CURRENT_USE = ContextVar('memory_use_guard', default=None)


class MemoryUseChanged(RuntimeError):
    pass


class MemoryUseState:
    def __init__(self, directory):
        self.path = Path(directory) / 'memory_use_state.json'
        self._seen = self.path.exists()

    def snapshot(self):
        return self.read_state()['epoch']

    def read_state(self):
        try:
            state = json.loads(self.path.read_text(encoding='utf-8'))
        except FileNotFoundError:
            if self._seen:
                raise MemoryUseChanged('memory_use_state_unavailable') from None
            return {'version':1, 'epoch':'0', 'dependencies':{}, 'policies':{}, 'history_floor':None}
        if (not isinstance(state, dict) or type(state.get('version')) is not int or state['version'] not in {1,2}
                or not isinstance(state.get('epoch'), str) or not re.fullmatch(r'(?:0|[0-9a-f]{32})',state['epoch'])):
            raise MemoryUseChanged('memory_use_state_unavailable')
        if (not isinstance(state.get('dependencies',{}),dict) or len(state.get('dependencies',{}))>10000
                or not isinstance(state.get('policies',{}),dict) or len(state.get('policies',{}))>10000
                or any(type(v) is not bool for v in state.get('policies',{}).values())
                or (state.get('history_floor') is not None and not re.fullmatch(r'[0-9a-f]{32}',str(state['history_floor'])))):
            raise MemoryUseChanged('memory_use_state_unavailable')
        self._seen = True
        return state

    def _save(self, state):
        # Old v1 readers must reject the richer privacy policy rather than
        # silently dropping dependencies, exclusions or the history barrier.
        state['version']=2
        atomic_save_json(self.path, state)
        self._seen = True

    def advance(self):
        # Caller holds the existing KB write lock. Invalidate before committing;
        # even a later failed mutation must not revive an older in-flight read.
        state = self.read_state()
        epoch = uuid4().hex
        state.update(epoch=epoch, changed=time.time())
        self._save(state)
        return epoch

    def register_dependencies(self, target_id, target_version, sources):
        state = self.read_state()
        dependencies = state.setdefault('dependencies',{})
        if target_id not in dependencies and len(dependencies)>=10000:
            raise MemoryUseChanged('memory_dependency_limit')
        dependencies[target_id] = {'target_version':target_version,'sources':dict(sources),'epoch':state['epoch']}
        self._save(state)

    def set_block(self, memory_id, enabled):
        if type(enabled) is not bool:raise ValueError('invalid_use_policy')
        state = self.read_state();policies=state.setdefault('policies',{})
        if memory_id in policies and policies[memory_id] is enabled:return state['epoch']
        if memory_id not in policies and len(policies)>=10000:raise MemoryUseChanged('memory_policy_limit')
        epoch=uuid4().hex
        policies[memory_id]=enabled;state.update(epoch=epoch,changed=time.time())
        if enabled:state['history_floor']=uuid4().hex
        self._save(state)
        return epoch

    def history_floor(self):
        return self.read_state().get('history_floor')

    def project_entries(self, entries):
        state = self.read_state();epoch=state['epoch'];floor=state.get('history_floor')
        # Deleting a memory does not delete the user's original conversation.
        # Older assistant replies may derive from an edited/deleted record and
        # must not be reused. A separate "do not use history" action is stricter.
        return [x for x in entries if (not floor or x.get('memory_history_floor')==floor)
            and (epoch=='0' or x.get('role')=='user' or x.get('memory_use_epoch')==epoch)]

    @contextmanager
    def bind(self):
        state = {'journal': self, 'epoch': self.snapshot(), 'active': True}
        token = _CURRENT_USE.set(state)
        try:
            yield state['epoch']
        finally:
            state['active'] = False
            _CURRENT_USE.reset(token)


def checkpoint_use_current():
    state = _CURRENT_USE.get()
    # Deferred work may outlive the stream; its captured version still fences
    # it after a later edit, without disabling unchanged-era background triage.
    if state is not None and state['journal'].snapshot() != state['epoch']:
        raise MemoryUseChanged('memory_context_changed')


def current_use_epoch():
    state = _CURRENT_USE.get()
    checkpoint_use_current()
    return state['epoch'] if state is not None else None


def current_history_floor():
    state=_CURRENT_USE.get()
    checkpoint_use_current()
    return state['journal'].history_floor() if state is not None else None
