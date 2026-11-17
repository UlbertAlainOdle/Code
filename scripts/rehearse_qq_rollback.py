"""Create a private isolated historical-source/data rehearsal, never restore live files."""
import argparse
import json
from pathlib import Path
import re
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from saya_app.services.qq_rollback import build_source_bundle,prepare_data,run_baseline


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--name',required=True)
    parser.add_argument('--include-private-data',action='store_true')
    parser.add_argument('--run-tests',action='store_true')
    args=parser.parse_args()
    if not re.fullmatch('[a-z0-9][a-z0-9-]{0,63}',args.name):parser.error('use a new short lowercase name')
    target=ROOT/'.runtime/qq-upgrade/rollback-rehearsals'/args.name
    manifest=json.loads((ROOT/'docs/qq-baseline-manifest-2026-10-26.json').read_text(encoding='utf-8'))
    try:
        report=build_source_bundle(ROOT,ROOT/'.runtime/qq-upgrade/2026-10-27-baseline/source',manifest,target/'code')
        if args.include_private_data:report['data']=prepare_data(ROOT,target/'private-data')
        if args.run_tests:report['baseline']=run_baseline(target/'code')
        (target/'rehearsal-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(report,ensure_ascii=False))
        return 0 if report.get('baseline',{}).get('exit_code',0)==0 else 1
    except Exception as error:
        print(json.dumps({'result':'not_completed','error_type':type(error).__name__,'live_files_restored':False}))
        return 1


if __name__=='__main__':raise SystemExit(main())
