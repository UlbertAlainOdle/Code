"""Request-local write authority supplied by trusted ingress, never an action."""
from contextlib import contextmanager
from contextvars import ContextVar

_current = ContextVar('memory_write_authority', default=None)


@contextmanager
def bind_write_authority(check):
    state = {'active': True, 'check': check}
    token = _current.set(state)
    try:
        yield
    finally:
        state['active'] = False
        _current.reset(token)


def check_write_authority():
    from .memory_v4.models import ValidationError
    state = _current.get()
    if state is not None and (not state['active'] or not state['check']()):
        raise ValidationError('native_write_authorization_changed')
