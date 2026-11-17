"""Prepare blind fictional pairs only after complete, equivalent 48-case runs.

This tool computes lengths, not naturalness/quality. Never turns missing cases
or a fake transport into a passed experience gate. No model or QQ calls.
"""
import argparse,hashlib,json,random,statistics
from pathlib import Path


def compare(baseline,candidate,directory):
    for data in (baseline,candidate):
        if data.get('result')!='PASS_GENERATION' or len(data.get('cases',[]))!=48 or not all(x.get('valid') for x in data['cases']):
            raise ValueError('complete_48_case_runs_required')
    for key in ('model_fingerprint','persona_fingerprint','profile_style_fingerprints','cases_fingerprint','thinking','max_output_tokens','lab_adapter_version','clock_fixture'):
        if key not in baseline or baseline[key]!=candidate.get(key):raise ValueError('conditions_differ:'+key)
    b={x['id']:x for x in baseline['cases']};c={x['id']:x for x in candidate['cases']}
    if len(b)!=48 or b.keys()!=c.keys():raise ValueError('case_ids_differ')
    rng=random.Random(77);blind=[];key={};lengths={}
    for index,identifier in enumerate(sorted(b)):
        left,right=b[identifier],c[identifier]
        if (left['question'],left['history'],left['group'])!=(right['question'],right['history'],right['group']):raise ValueError('case_inputs_differ')
        swapped=bool(rng.getrandbits(1));pair_id='pair-'+str(index+1)
        answers=[left['text'],right['text']]
        if swapped:answers.reverse()
        blind.append({'pair_id':pair_id,'question':left['question'],'history':left['history'],'A':answers[0],'B':answers[1],
            'review':{'preferred':None,'A_relevance_1_to_5':None,'B_relevance_1_to_5':None,
                'A_completeness_1_to_5':None,'B_completeness_1_to_5':None,'A_naturalness_1_to_5':None,'B_naturalness_1_to_5':None,'notes':''}})
        key[pair_id]={'case_id':identifier,'A':'candidate' if swapped else 'baseline'}
    for group in sorted({x['group'] for x in b.values()}):
        old=statistics.median(x['chars'] for x in b.values() if x['group']==group)
        new=statistics.median(x['chars'] for x in c.values() if x['group']==group)
        lengths[group]={'baseline_median':old,'candidate_median':new,'reduction':1-new/old if old else None}
    simple_old=statistics.median(x['chars'] for x in b.values() if x['group'] in {'casual','sharing'})
    simple_new=statistics.median(x['chars'] for x in c.values() if x['group'] in {'casual','sharing'})
    report={'result':'LENGTH_COMPARISON_ONLY_REVIEW_PENDING','cases':48,'groups':lengths,
        'simple_median_reduction':1-simple_new/simple_old if simple_old else None,
        'naturalness_and_completeness':'independent blinded review required',
        'media_selection':'separate selector evaluation required','actual_audio_or_QQ_receipt':'not_proven'}
    directory=Path(directory);directory.mkdir(exist_ok=False)
    for name,value in [('blind-pairs.json',blind),('private-answer-key.json',key),('comparison.json',report)]:
        (directory/name).write_text(json.dumps(value,ensure_ascii=False,indent=2),'utf8')
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('baseline');parser.add_argument('candidate');parser.add_argument('output');args=parser.parse_args()
    root=Path(__file__).resolve().parents[1]/'.runtime/qq-upgrade'
    output=Path(args.output).resolve()
    if not output.is_relative_to(root):parser.error('isolated private output required')
    print(json.dumps(compare(json.loads(Path(args.baseline).read_text('utf8')),json.loads(Path(args.candidate).read_text('utf8')),output),indent=2))
