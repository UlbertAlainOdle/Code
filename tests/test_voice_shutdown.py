from unittest.mock import Mock, patch

import pytest

from saya_app.services.process_watch import watch_owned_processes


@pytest.mark.parametrize('ends', ['owner', 'worker'])
def test_guardian_cleans_after_either_owned_process_exits(ends):
    owner = Mock(birth=11)
    worker = Mock(birth=22)
    owner.alive.side_effect = [True, ends != 'owner']
    worker.alive.side_effect = [True, False]
    cleanup, ready, sleep = Mock(), Mock(), Mock()
    with patch('saya_app.services.process_watch.WindowsProcess', side_effect=[owner, worker]):
        watch_owned_processes(100, 11, 200, 22, cleanup, ready, sleep)
    cleanup.assert_called_once_with()
    ready.assert_called_once_with()
    sleep.assert_called_once_with(0.5)
    owner.close.assert_called_once_with()
    worker.close.assert_called_once_with()


def test_reused_parent_pid_does_not_keep_voice_alive():
    reused = Mock(birth=99)
    cleanup, ready = Mock(), Mock()
    with patch('saya_app.services.process_watch.WindowsProcess', return_value=reused):
        watch_owned_processes(100, 11, 200, 22, cleanup, ready, Mock())
    cleanup.assert_called_once_with()
    ready.assert_not_called()
    reused.close.assert_called_once_with()


def test_parent_already_gone_is_cleaned_before_startup():
    cleanup = Mock()
    with patch('saya_app.services.process_watch.WindowsProcess', side_effect=ProcessLookupError):
        watch_owned_processes(100, 11, 200, 22, cleanup, Mock(), Mock())
    cleanup.assert_called_once_with()


def test_access_denial_is_not_mistaken_for_parent_exit():
    cleanup = Mock()
    with patch('saya_app.services.process_watch.WindowsProcess', side_effect=PermissionError):
        with pytest.raises(PermissionError):
            watch_owned_processes(100, 11, 200, 22, cleanup, Mock(), Mock())
    cleanup.assert_not_called()
