import json
from unittest.mock import patch

import pytest

from saya_app.services.qq_observation import ObservationLedger


HEALTHY={'saya':'ready','consumer':'running','napcat':'reachable','onebot_auth':'accepted','qq_login':'matched'}


def test_only_measured_intervals_count_and_sleep_gaps_never_become_uptime(tmp_path):
    ledger=ObservationLedger(tmp_path/'observation.json')
    ledger.record(HEALTHY,100.)
    data=ledger.record(HEALTHY | {'api_key':'secret','user_id':'private','text':'do not retain'},160.)
    assert data['healthy_seconds']==60 and data['measured_seconds']==60
    data=ledger.record(HEALTHY,10000.)
    assert data['healthy_seconds']==60 and data['unknown_seconds']==9840
    assert not data['ready_for_24h_review']
    assert all(word not in ledger.path.read_text() for word in ('secret','private','retain'))
    summary=ObservationLedger(ledger.path).summary(10100.)
    assert summary['stale'] and summary['unknown_seconds']==9940.
    assert summary['unobserved_tail_seconds']==100.


def test_health_transitions_are_bounded_and_missing_login_is_not_healthy(tmp_path):
    ledger=ObservationLedger(tmp_path/'observation.json',max_events=4)
    for index in range(12):
        data=ledger.record(HEALTHY | {'qq_login':'matched' if index%2 else 'login_required'},100.+index*60)
    assert data['healthy_seconds']==0  # Neither end-to-end interval has both healthy endpoints.
    assert len(data['events'])==4
    assert any(event['state']['qq_login']=='login_required' for event in data['events'])
    assert 'source' not in json.dumps(data)


def test_a_full_day_measured_is_only_ready_for_manual_review_and_no_automatic_pass(tmp_path):
    ledger=ObservationLedger(tmp_path/'observation.json')
    for instant in range(0,86401,60): data=ledger.record(HEALTHY,float(instant))
    assert data['ready_for_24h_review']
    assert data['measured_seconds']==86400 and data['healthy_seconds']==86400
    assert data['gate']=='manual_review_required'
    before=ledger.path.read_bytes()
    with patch('saya_app.services.qq_observation.atomic_save_json',side_effect=OSError('disk')):
        with pytest.raises(OSError): ledger.record(HEALTHY,86460.)
    assert ledger.path.read_bytes()==before


def test_wall_clock_regression_never_double_counts_an_interval(tmp_path):
    ledger=ObservationLedger(tmp_path/'observation.json')
    ledger.record(HEALTHY,100.)
    ledger.record(HEALTHY,160.)
    data=ledger.record(HEALTHY,130.)
    assert data['last_sample']==160. and data['clock_regressions']==1
    assert ledger.record(HEALTHY,220.)['healthy_seconds']==120.
