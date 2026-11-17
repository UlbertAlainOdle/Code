"""Small real-model retrieval smoke corpus, isolated from the user's memories."""
import json
from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core_services import KnowledgeBase

CASES = [
    ('我最喜欢的动物是乌鸦。', ['我最喜欢哪种鸟？', 'What is my favorite animal?']),
    ('我常用的代码编辑器是 Neovim。', ['我写代码用哪个编辑工具？', 'Which editor do I use to write code?']),
    ('我偏好在安静的环境中工作。', ['工作环境嘈杂适合我吗？', 'Do I prefer a quiet workspace?']),
    ('我习惯喝不加糖的冰咖啡。', ['我喝咖啡会加糖吗？', 'Which drink do I take without sweetener?']),
    ('Lantern 项目使用 Docker Compose 部署。', ['Lantern 服务怎样部署？', 'How is the Lantern project deployed?']),
    ('我出生在南京。', ['我的出生地是哪里？', 'Where was I born?']),
    ('我每周三在健身房练习力量训练。', ['星期三我通常做什么锻炼？', 'What exercise do I do on Wednesdays?']),
    ('我希望回复简洁，并保留必要的推理依据。', ['我希望回复采用什么风格？', 'How much detail should your replies contain?']),
]


def main():
    with tempfile.TemporaryDirectory(prefix='memory-semantic-eval-') as directory:
        kb = KnowledgeBase(directory)
        ids = []
        for fact, _ in CASES:
            ids.append(kb.capture_explicit_memory('记住：' + fact)['id'])
        started = time.perf_counter()
        indexed = kb.build_embedding_index()
        rows = []
        for expected, (_, queries) in zip(ids, CASES):
            for query in queries:
                start = time.perf_counter()
                result = kb.query_memory_v4(query, bypass_need_planner=True, max_items=3)
                actual = [item['id'] for item in result['items']]
                rank = actual.index(expected)+1 if expected in actual else None
                rows.append({'query':query, 'rank':rank, 'latency_ms':round((time.perf_counter()-start)*1000, 1)})
        unknown = kb.query_memory_v4('我弟弟的电话号码是什么？', bypass_need_planner=True, max_items=3)
        kb.embedding_provider.close()
        report = {'model':'paraphrase-multilingual-MiniLM-L12-v2', 'queries':len(rows),
                  'index':indexed['status'], 'index_seconds':round(time.perf_counter()-started, 2),
                  'recall_at_3':sum(r['rank'] is not None for r in rows)/len(rows),
                  'mrr_at_3':sum(1/r['rank'] if r['rank'] else 0 for r in rows)/len(rows),
                  'unknown_returned_count':len(unknown['items']), 'results':rows,
                  'limitation':'Synthetic smoke corpus; no paid answer model or long-horizon benchmark.'}
        target = ROOT / 'docs' / 'experiments' / 'memory-default-semantic-eval.json'
        target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps({k:v for k,v in report.items() if k != 'results'}, ensure_ascii=True))


if __name__ == '__main__':
    main()
