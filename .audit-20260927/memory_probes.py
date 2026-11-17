import run_tests
import json,tempfile
from pathlib import Path
from core_services import KnowledgeBase
from saya_app.services.memory_v4 import MemoryV4Store,MemoryItemV4,MemoryEvidence,MemoryRetriever,MemoryQuery
result={}
with tempfile.TemporaryDirectory(dir=run_tests.ROOT/'.audit-20260927') as root:
    store=MemoryV4Store(Path(root)/'v4.sqlite3')
    text='Doctor prefers concise answers.'
    item=MemoryItemV4(id='fixture',subject='user',predicate='prefers',value=text,canonical_text=text,kind='knowledge',authority='user_confirmed',sensitivity='normal')
    evidence=MemoryEvidence(id='e-fixture',memory_id='fixture',source_type='user_message',source_id='fixture',role='assertion',quote=text)
    store.upsert_memory(item,[evidence])
    for query in ['Doctor prefers concise answers','brief responses','回答简短一些']:
        result[query]=[i.id for i in MemoryRetriever(store).search(MemoryQuery(query=query)).items]
    kb=KnowledgeBase(str(Path(root)/'kb'))
    kb.update_settings({'memory_background_synthesis_enabled':True,'memory_v4_write_enabled':True})
    result['synthesis']=kb.schedule_memory_v4_synthesis()
(run_tests.ROOT/'.audit-20260927/memory-probes.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(result,ensure_ascii=False,indent=2))
