import importlib.util
from pathlib import Path
from types import SimpleNamespace
import sys
import pytest

SCRIPTS=Path(__file__).resolve().parents[1]/'scripts'
sys.path.insert(0,str(SCRIPTS))
spec=importlib.util.spec_from_file_location('qq_one_click',SCRIPTS/'start_saya_qq.py')
launcher=importlib.util.module_from_spec(spec);spec.loader.exec_module(launcher)


def fake(healths,*,busy=False,owned=True,nap='running_owned'):
    calls=[];values=iter(healths)
    return SimpleNamespace(ROOT=Path('.'),RUNNER=Path('runner.py'),calls=calls,
        port_open=lambda p:busy,read_record=lambda n:{},owns=lambda *a:owned,
        NapcatSupervisor=lambda r:SimpleNamespace(start_once=lambda:calls.append('napcat') or {'phase':nap}),
        start=lambda p:calls.append('backend'),probe_health=lambda p:next(values))


READY={'saya':'ready','consumer':'running','napcat':'reachable','qq_login':'matched'}


def test_cannot_claim_complete_before_receiver_and_account_ready():
    m=fake([{},READY|{'qq_login':'unknown'},READY])
    assert launcher.start_system(m,sleep=lambda t:None)==READY
    assert m.calls==['napcat','backend']


def test_foreign_port_is_preserved_without_starting_anything():
    m=fake([],busy=True,owned=False)
    with pytest.raises(RuntimeError,match='another backend'):launcher.start_system(m)
    assert m.calls==[]


@pytest.mark.parametrize('values,match',[
    (READY|{'consumer':'paused'},'disabled'),
    (READY|{'qq_login':'wrong_account'},'does not match'),
    (READY|{'qq_login':'login_required'},'phone confirmation')])
def test_attention_states_never_report_complete_or_overwrite_settings(values,match):
    m=fake([values])
    with pytest.raises(RuntimeError,match=match):launcher.start_system(m)
    assert m.calls==['napcat','backend']


def test_pending_timeout_preserves_process_and_does_not_relaunch():
    m=fake([{}]);ticks=iter([0,0,2])
    with pytest.raises(RuntimeError,match='pending'):launcher.start_system(m,timeout=1,clock=lambda:next(ticks),sleep=lambda t:None)
    assert m.calls==['napcat','backend']


def test_running_owned_entry_reuses_existing_start_paths():
    m=fake([READY],busy=True,owned=True)
    assert launcher.start_system(m)==READY and m.calls==['napcat','backend']


def test_napcat_binding_failure_does_not_start_backend():
    m=fake([],nap='install_invalid')
    with pytest.raises(RuntimeError,match='not confirmed'):launcher.start_system(m)
    assert m.calls==['napcat']
