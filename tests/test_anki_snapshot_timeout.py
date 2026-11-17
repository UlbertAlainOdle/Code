"""Real SQLite locks must not keep the service drain gate occupied forever."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import sqlite3
import time

from saya_app.services.anki import AnkiService
from test_anki import create_collection


def test_busy_snapshot_times_out_preserves_statistics_and_recovers_after_unlock(tmp_path):
    source=tmp_path/'Anki2'/'User 1'/'collection.anki2'
    create_collection(source)
    service=AnkiService(tmp_path/'knowledge_base')
    previous=service.configure(source)
    assert previous['status']=='connected'
    service.SNAPSHOT_TIMEOUT_SECONDS=.15
    original=hashlib.sha256(source.read_bytes()).hexdigest()
    holder=sqlite3.connect(source)
    holder.execute('BEGIN EXCLUSIVE')
    started=time.monotonic()
    executor=ThreadPoolExecutor(max_workers=1)
    try:
        # An unbounded implementation fails at result(timeout), then the
        # finally block releases the test lock so the process can still exit.
        result=executor.submit(service.refresh).result(timeout=3)
        assert time.monotonic()-started<2
        assert result['status']=='stale' and not result['connected']
        assert result['summary']==previous['summary']
        assert result['settings']==previous['settings']
        assert 'snapshot timed out' in result['error']
        assert not list(service.snapshot_dir.glob('*.anki2'))
    finally:
        holder.rollback();holder.close();executor.shutdown(wait=True)
    assert hashlib.sha256(source.read_bytes()).hexdigest()==original
    recovered=service.refresh()
    assert recovered['status']=='connected' and not recovered['stale']
    assert recovered['summary']==previous['summary']
    assert not list(service.snapshot_dir.glob('*.anki2'))
    assert hashlib.sha256(source.read_bytes()).hexdigest()==original


def test_first_locked_read_returns_error_without_clearing_configuration(tmp_path):
    source=tmp_path/'Anki2'/'User 1'/'collection.anki2'
    create_collection(source)
    service=AnkiService(tmp_path/'knowledge_base')
    service._settings={'data_path':str(source.parent.parent),'profile':'User 1'}
    service.SNAPSHOT_TIMEOUT_SECONDS=.15
    holder=sqlite3.connect(source);holder.execute('BEGIN EXCLUSIVE')
    executor=ThreadPoolExecutor(max_workers=1)
    try:
        result=executor.submit(service.refresh).result(timeout=3)
        assert result['status']=='error' and result['configured']
        assert service._settings['profile']=='User 1'
        assert not list(service.snapshot_dir.glob('*.anki2'))
    finally:
        holder.rollback();holder.close();executor.shutdown(wait=True)
