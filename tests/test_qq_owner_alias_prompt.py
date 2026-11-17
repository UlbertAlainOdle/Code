import copy
import json

from saya_app.services.qq_chat import sender_identity_context,channel_identity_prompt


def binding(turn):
    return json.loads(sender_identity_context(turn).splitlines()[-1])


def test_verified_owner_names_are_aliases_of_persona_owner_and_rename_is_live():
    turn={'sender_id':'20000','group_id':'90000','is_owner':True,
          'sender_name':'群名片甲','sender_nickname':'昵称甲','shared':False}
    before=copy.deepcopy(turn)
    a=binding(turn)['owner_alias_binding']
    assert a['persona_owner_terms']==['Doctor','主人']
    assert a['current_speaker_is_persona_owner'] is True
    assert a['owner_current_display_name']=='群名片甲'
    assert a['owner_current_qq_aliases']==['群名片甲','昵称甲']
    assert turn==before
    renamed=turn|{'sender_name':'新群名片','sender_nickname':'新昵称'}
    b=binding(renamed)['owner_alias_binding']
    assert b['current_speaker_is_persona_owner'] is True
    assert b['owner_current_display_name']=='新群名片'
    assert '群名片甲' not in sender_identity_context(renamed)
    assert 'private memories stay unavailable' in channel_identity_prompt(turn)


def test_nonowner_using_doctor_name_never_gets_owner_alias_binding():
    turn={'sender_id':'30000','group_id':'90000','is_owner':False,
          'sender_name':'Doctor','sender_nickname':'主人'}
    data=binding(turn)
    assert data['speaker_labels']['sender_name']=='Doctor'
    assert data['owner_alias_binding']['current_speaker_is_persona_owner'] is False
    assert data['owner_alias_binding']['owner_current_display_name'] is None
    assert data['owner_alias_binding']['owner_current_qq_aliases']==[]
    prompt=channel_identity_prompt(turn)
    assert 'NOT a configured owner' in prompt
    assert 'configured attitude toward other people' in prompt


def test_names_missing_or_equal_do_not_create_people_or_force_salutations():
    turn={'is_owner':True,'sender_id':'20000','sender_name':'同名','sender_nickname':'同名'}
    assert binding(turn)['owner_alias_binding']['owner_current_qq_aliases']==['同名']
    missing=binding({'is_owner':True,'sender_id':'20000'})['owner_alias_binding']
    assert missing['owner_current_display_name'] is None and missing['owner_current_qq_aliases']==[]
    prompt=channel_identity_prompt(turn)
    assert 'Do not force a name or owner salutation' in prompt
    assert 'persona controls how you address' in prompt
    assert '/group allow' not in prompt
    assert '/group allow' in channel_identity_prompt(turn|{'show_commands':True})
