from scripts.eval_qq_recall_r4 import seed
from saya_app.services.memory_recall_reference import resolve, still_valid


def history(ids):
    return [{'role':'ai','content':'UNTRUSTED invented descriptor', 'memory_recall':{
        'evidence_contract':{'grounded_literal_focus':focus},'selected':[{'id':ids[key]} for key in keys]}}
        for focus,keys in [('青岚计划',['choice','reason']),('棠木计划',['no_reason'])]]


def test_return_grounds_in_current_records_not_latest_topic_or_ai_words(tmp_path):
    kb,ids=seed(tmp_path)
    result=resolve(kb,'回到刚才那个海上作业的计划，我为什么作了那种选择？',history(ids))
    assert result['focus']=='青岚计划'
    assert set(result['source_ids'])=={ids['choice'],ids['reason']}
    assert not result['history_is_authority']
    assert resolve(kb,'回到那个UNTRUSTED invented descriptor的计划',history(ids)) is None


def test_revoked_or_changed_descriptor_cannot_resolve_old_reference(tmp_path):
    kb,ids=seed(tmp_path)
    kb.set_memory_use_policy(ids['reason'],True)
    assert resolve(kb,'回到刚才那个海上作业的计划，我为什么选择它？',history(ids)) is None
    kb.delete_memory(ids['choice'])
    assert resolve(kb,'回到刚才那个海上作业的计划，我为什么选择它？',history(ids)) is None


def test_underspecified_or_quoted_return_never_picks_latest(tmp_path):
    kb,ids=seed(tmp_path)
    assert resolve(kb,'回到刚才那个计划，我为什么选择它？',history(ids)) is None
    assert resolve(kb,'解释“回到海上作业的计划”这句话',history(ids)) is None


def test_ambiguous_descriptors_are_not_resolved(tmp_path):
    kb,ids=seed(tmp_path)
    row=kb.add_memory('虚构棠木计划：海上作业经常断网，所以选择木结构。',title='reason')
    rows=history(ids);rows[-1]['memory_recall']['selected'].append({'id':row['id']})
    assert resolve(kb,'回到刚才那个海上作业的计划，我为什么选择它？',rows) is None


def test_resolution_is_invalidated_when_source_changes(tmp_path):
    kb,ids=seed(tmp_path)
    result=resolve(kb,'回到刚才那个海上作业的计划，我为什么选择它？',history(ids))
    assert still_valid(kb,result)
    kb.update_memory(ids['reason'],{'content':'虚构青岚计划的新理由：这次是成本限制。'})
    assert not still_valid(kb,result)


def test_history_does_not_grant_project_scope(tmp_path):
    kb,ids=seed(tmp_path)
    rows=[{'role':'ai','memory_recall':{'evidence_contract':{'grounded_literal_focus':'项目青一'},
                                      'selected':[{'id':ids['project_alpha']}]}}]
    question='回到木制接口那个项目，我为什么选择它？'
    assert resolve(kb,question,rows) is None
    assert resolve(kb,question,rows,scope_type='project',scope_id='alpha')['focus']=='项目青一'
