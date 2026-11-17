"""Conservative return-reference grounding in current readable records."""
import re
import hashlib

from .memory_recall_contract import explicit_self_choice, single_literal_focus
from .memory_v4 import MemoryQuery, MemoryRetriever
from .memory_v4.retrieval import _tokens


_AVAILABLE = r'(?:能用|可用|能使用|可使用)'
_EXCLUSIVE_RESOURCE = re.compile(
    r'(?:只剩|仅剩|只有|仅有)\s*[一1][种类]\s*([\u3400-\u9fff]{2,8}?)\s*' + _AVAILABLE)


def _only_option_record(text, resource):
    pattern = r'(?:只有|仅有|只剩|仅剩)\s*([^\s，,。！？；;]{1,10}?)\s*' + re.escape(resource) + r'\s*' + _AVAILABLE
    return any(not re.search(r'和|或|与|及|、|多|几|两|二|三|四|[2-9]', qualifier)
               for qualifier in re.findall(pattern, text))


def inspect(kb, question, history, *, scope_type=None, scope_id=None):
    text=str(question or '')[:2000]
    if not re.search(r'回到|说回|再说回|back to',text,re.I) or re.search(r'[\n\r"“”「」『』]',text):
        return None
    if re.search(r'分别|比较|对比|\bboth\b|\bcompare\b',text,re.I):return None
    store=kb._open_memory_v4_store_for_read()
    if store is None:return None
    groups={}
    for row in list(history or [])[-40:]:
        if row.get('role') not in {'ai','assistant'}:continue
        audit=row.get('memory_recall') or {}
        focus=(audit.get('evidence_contract') or {}).get('grounded_literal_focus')
        if not isinstance(focus,str) or not 4<=len(focus)<=48:continue
        groups.setdefault(focus,set()).update(str(item.get('id')) for item in audit.get('selected',[])
                                             if isinstance(item,dict) and item.get('id'))
    named=single_literal_focus(re.sub(r'^(?:再)?(?:回到|说回)\s*','',text))
    if named:
        current=kb.query_memory_v4(named,bypass_need_planner=True,scope_type=scope_type,scope_id=scope_id,
                                  max_candidates=80,max_items=20)
        ids={item['id'] for item in current.get('items',[]) if named in item.get('canonical_text','')}
        if ids:groups.setdefault(named,set()).update(ids)
    if not groups or len(groups)>8:
        return {'status':'clarify','candidates':[],'reason':'no_bounded_history_candidates'}
    reader=MemoryRetriever(store,permitted=kb.memory_allowed_for_use)
    query=MemoryQuery(query=text,scope_type=scope_type,scope_id=scope_id)
    generic=_tokens('回到刚才那个之前当时计划项目方案试验活动为什么理由选择处理我那种作了请再说一遍')
    terms={token for token in _tokens(text)-generic if len(token)>=2}
    exclusive = _EXCLUSIVE_RESOURCE.search(text)
    scored=[]
    allowed_names=[]
    for focus,ids in groups.items():
        items=reader.fetch_full(sorted(ids)[:100],query=query)
        items=[item for item in items if focus in item.canonical_text and
               (not explicit_self_choice(text) or item.subject=='user')]
        evidence=' '.join(item.canonical_text for item in items)
        if items:allowed_names.append(focus)
        matches=(terms-_tokens(focus)) & _tokens(evidence)
        if exclusive:
            constrained = [item for item in items if _only_option_record(item.canonical_text, exclusive[1])]
            if constrained:
                scored.append((len(matches), focus, constrained))
        elif len(matches)>=2 or focus in text:
            scored.append((len(matches),focus,items))
    scored.sort(reverse=True)
    if len(scored)!=1 or (exclusive and len(scored[0][2]) != 1):
        return {'status':'clarify','candidates':sorted(allowed_names),
                'reason':'multiple_descriptor_matches' if scored else 'insufficient_current_descriptor'}
    return {'status':'resolved','focus':scored[0][1],'source_ids':[item.id for item in scored[0][2]],
            'source_fingerprints':{item.id:fingerprint(item) for item in scored[0][2]},
            'method':'unique_exclusive_resource' if exclusive else 'unique_current_record_descriptor',
            'restrict_to_source_ids':bool(exclusive),'history_is_authority':False}


def resolve(kb, question, history, **kwargs):
    result=inspect(kb,question,history,**kwargs)
    return result if result and result['status']=='resolved' else None


def clarification(plan):
    names=plan.get('candidates',[])[:8]
    return ('你想返回哪一个话题：'+ '、'.join(names)+'？请明确名称或再补一点描述，我再核对对应理由。'
            if names else '你想返回哪个话题？目前没有足够的可用依据确定对象，请明确名称或补充描述。')


def fingerprint(item):
    return hashlib.sha256((str(item.revision)+'\n'+item.canonical_text).encode('utf-8')).hexdigest()


def still_valid(kb, reference, *, scope_type=None, scope_id=None):
    store=kb._open_memory_v4_store_for_read()
    if store is None:return False
    items=MemoryRetriever(store,permitted=kb.memory_allowed_for_use).fetch_full(reference['source_ids'],
        query=MemoryQuery(scope_type=scope_type,scope_id=scope_id))
    return {item.id:fingerprint(item) for item in items}==reference['source_fingerprints']
