"""The evidence helper must refuse incomplete or mismatched fictional runs."""
import copy,importlib.util,json
from pathlib import Path
import pytest

spec=importlib.util.spec_from_file_location('qq_experience_compare',Path(__file__).resolve().parents[1]/'scripts/qq_dialogue_experience_compare.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


def fixture():
    value={'result':'PASS_GENERATION','model_fingerprint':'a','persona_fingerprint':'b','profile_style_fingerprints':{},
        'cases_fingerprint':'c','thinking':'auto','max_output_tokens':8192,'lab_adapter_version':2,'clock_fixture':'fixed_per_case_Asia_Shanghai_v1',
        'cases':[{'id':str(i),'valid':True,'question':'虚构问题','history':[],'group':'casual' if i<24 else 'complex','text':'虚构回答','chars':100} for i in range(48)]}
    return value


@pytest.mark.parametrize('mutation',[lambda x:x['cases'].pop(),lambda x:x.update(model_fingerprint='different'),
    lambda x:x['cases'][0].update(valid=False),lambda x:x.pop('persona_fingerprint'),
    lambda x:x['cases'][0].update(question='不同输入')])
def test_incomplete_or_changed_inputs_cannot_produce_passed_evidence(tmp_path,mutation):
    a,b=fixture(),fixture();mutation(b)
    with pytest.raises(ValueError):module.compare(a,b,tmp_path/'report')
    assert not (tmp_path/'report').exists()


def test_blind_length_report_does_not_claim_quality_or_actual_media(tmp_path):
    a,b=fixture(),fixture()
    for x in b['cases']:x.update(chars=50,text='短虚构回答')
    report=module.compare(a,b,tmp_path/'report')
    assert report['simple_median_reduction']==.5
    assert report['result']=='LENGTH_COMPARISON_ONLY_REVIEW_PENDING'
    assert report['actual_audio_or_QQ_receipt']=='not_proven'
    pairs=json.loads((tmp_path/'report/blind-pairs.json').read_text('utf8'))
    assert len(pairs)==48 and all(x['review']['preferred'] is None for x in pairs)
    assert all('candidate' not in x and 'baseline' not in x for x in pairs)
    assert (tmp_path/'report/private-answer-key.json').is_file()


def test_lab_adapts_archived_roles_and_fixed_clock_before_real_generation():
    path=Path(__file__).resolve().parents[1]/'scripts/qq_dialogue_experience_lab.py'
    spec=importlib.util.spec_from_file_location('qq_lab_contract',path);lab=importlib.util.module_from_spec(spec);spec.loader.exec_module(lab)
    assert lab.canonical_history(['User: 方案','AI: 三天','Doctor: 原格式'])==['Doctor: 方案','Saya: 三天','Doctor: 原格式']
    assert lab.fictional_clock('casual',0)==lab.fictional_clock('voice',6)
    assert lab.fictional_clock('casual',6)>lab.fictional_clock('casual',0)
