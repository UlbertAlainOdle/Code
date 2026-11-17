from dataclasses import replace
import pytest
from saya_app.services.qq_memory_access import (
    QQMemoryAccessStore,MemoryActor,MemoryAccessConflict,MemoryAccessDenied,
    can_disclose,can_write)
from test_qq_tasks import Store


@pytest.fixture
def lab(tmp_path):
    store=QQMemoryAccessStore(Store(tmp_path/'acl.sqlite3'))
    owner=MemoryActor('10000','20000',True,True,write_enabled=True)
    return store,owner


def policy(lab,**kwargs):
    store,owner=lab
    return store.set(owner,'memory-a',1,'20000',**kwargs)


def test_private_default_remembers_owner_privately_without_public_group_disclosure(lab):
    _,owner=lab;p=policy(lab)
    assert can_disclose(owner,p,'memory-a',1)
    group=replace(owner,group_id='90000',group_authorized=True)
    assert not can_disclose(group,p,'memory-a',1)
    assert not can_disclose(replace(owner,user_id='30000',is_owner=False),p,'memory-a',1)


def test_requester_and_audience_are_separate_gates(lab):
    _,owner=lab
    p=policy(lab,visibility='groups',groups=('90000',))
    group=replace(owner,group_id='90000',group_authorized=True)
    assert can_disclose(group,p,'memory-a',1)
    assert not can_disclose(replace(group,group_id='90001'),p,'memory-a',1)
    assert not can_disclose(replace(group,user_id='30000',is_owner=False),p,'memory-a',1)


def test_named_requester_grant_does_not_grant_other_accounts(lab):
    _,owner=lab;p=policy(lab,visibility='groups',groups=('90000',),requesters=('30000',))
    member=replace(owner,is_owner=False,user_id='30000',group_id='90000',group_authorized=True)
    assert can_disclose(member,p,'memory-a',1)
    assert not can_disclose(replace(member,user_id='40000'),p,'memory-a',1)
    assert can_disclose(replace(member,group_id=''),p,'memory-a',1)


def test_all_requester_is_explicit_and_still_requires_authorized_audience(lab):
    _,owner=lab;p=policy(lab,visibility='groups',groups=('90000',),requesters=('*',))
    member=replace(owner,is_owner=False,user_id='30000',group_id='90000',group_authorized=True)
    assert can_disclose(member,p,'memory-a',1)
    assert not can_disclose(replace(member,group_id='90001'),p,'memory-a',1)
    assert not can_disclose(replace(member,group_authorized=False),p,'memory-a',1)


@pytest.mark.parametrize('change',[{'verified':False},{'bot_id':'10001'},{'user_id':'Doctor'}, {'group_id':'90000','group_authorized':False}])
def test_untrusted_or_wrong_identity_does_not_read(lab,change):
    _,owner=lab;p=policy(lab,visibility='public',requesters=('*',))
    assert not can_disclose(replace(owner,**change),p,'memory-a',1)


def test_policy_cannot_be_reused_for_another_memory_revision_or_secret(lab):
    _,owner=lab;p=policy(lab,visibility='public',requesters=('*',))
    assert not can_disclose(owner,p,'other-memory',1)
    assert not can_disclose(owner,p,'memory-a',2)
    assert not can_disclose(owner,p,'memory-a',1,sensitivity='secret')
    assert not can_disclose(owner,p,'memory-a',1,sensitivity='unknown')
    assert not can_disclose(owner,None,'memory-a',1)


def test_owner_can_explicitly_save_own_fact_in_authorized_group(lab):
    _,owner=lab;p=policy(lab)
    group=replace(owner,group_id='90000',group_authorized=True)
    assert can_write(group,p,'memory-a',1,explicit=True,source_speaker_id='20000')
    assert not can_disclose(group,p,'memory-a',1)  # Save permission is not publication.
    assert not can_write(group,p,'memory-a',1,source_speaker_id='20000')
    assert not can_write(replace(group,write_enabled=False),p,'memory-a',1,explicit=True,source_speaker_id='20000')


def test_other_member_cannot_overwrite_owner_even_if_disclosure_is_public(lab):
    _,owner=lab;p=policy(lab,visibility='public',requesters=('*',),writers=('30000',))
    member=replace(owner,is_owner=False,user_id='30000',group_id='90000',group_authorized=True)
    assert can_disclose(member,p,'memory-a',1)
    assert not can_write(member,p,'memory-a',1,explicit=True,source_speaker_id='30000',target_group_id='90000')


def test_delegated_member_write_belongs_only_to_self_and_original_group(lab):
    store,owner=lab
    p=store.set(owner,'member-note',1,'30000',visibility='groups',groups=('90000',),writers=('30000',))
    member=replace(owner,is_owner=False,user_id='30000',group_id='90000',group_authorized=True)
    kw=dict(explicit=True,source_speaker_id='30000',target_group_id='90000')
    assert can_write(member,p,'member-note',1,**kw)
    assert not can_write(member,p,'member-note',1,**(kw|{'target_group_id':'90001'}))
    assert not can_write(member,p,'member-note',1,**(kw|{'source_speaker_id':'20000'}))
    assert not can_write(replace(member,user_id='40000'),p,'member-note',1,**kw)


def test_only_owner_private_management_can_publish_change_or_revoke(lab):
    store,owner=lab;p=policy(lab)
    for actor in [replace(owner,is_owner=False),replace(owner,verified=False),replace(owner,group_id='90000',group_authorized=True)]:
        with pytest.raises(MemoryAccessDenied):store.set(actor,'memory-a',1,'20000',expected_revision=p.revision,visibility='public')
    with pytest.raises(MemoryAccessConflict):store.set(owner,'memory-a',1,'20000',expected_revision=0,visibility='public')
    public=store.set(owner,'memory-a',1,'20000',expected_revision=1,visibility='public',requesters=('*',))
    assert public.revision==2
    private=store.set(owner,'memory-a',1,'20000',expected_revision=2)
    member=replace(owner,user_id='30000',is_owner=False)
    assert not can_disclose(member,private,'memory-a',1)
    assert store.get('10001','memory-a') is None


def test_revoked_policy_or_changed_memory_invalidates_inflight_disclosure(lab):
    store,owner=lab;p=policy(lab,visibility='groups',groups=('90000',),requesters=('*',))
    member=replace(owner,is_owner=False,user_id='30000',group_id='90000',group_authorized=True)
    grant=store.authorize_disclosure(member,'memory-a',1)
    assert grant and store.valid_disclosure(member,grant,1)
    assert not store.valid_disclosure(member,grant,2)
    assert not store.valid_disclosure(replace(member,user_id='40000'),grant,1)
    assert not store.valid_disclosure(replace(member,group_authorized=False),grant,1)
    store.set(owner,'memory-a',1,'20000',expected_revision=p.revision)
    assert not store.valid_disclosure(member,grant,1)
    assert store.authorize_disclosure(member,'memory-a',1) is None


def test_write_rechecks_current_delegation(lab):
    store,owner=lab
    p=store.set(owner,'member-note',1,'30000',visibility='groups',groups=('90000',),writers=('30000',))
    member=replace(owner,is_owner=False,user_id='30000',group_id='90000',group_authorized=True)
    kw=dict(explicit=True,source_speaker_id='30000',target_group_id='90000')
    assert store.authorize_write(member,'member-note',1,**kw)
    store.set(owner,'member-note',1,'30000',expected_revision=p.revision,visibility='groups',groups=('90000',))
    assert not store.authorize_write(member,'member-note',1,**kw)
