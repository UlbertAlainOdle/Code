"""Host-bound initial read and answer scope for named record-reason questions."""
import inspect
import re


def prepare_step(model_step,registry,options):
    from .memory_recall_contract import question_plan,single_literal_focus,personal_recall_intent
    question=str((options or {}).get('question_text') or '')
    focus=single_literal_focus(question)
    explanation=bool(re.search(r'一般(?:而言|来说|原理)|原理|机制|推测|猜测|可能(?:的)?原因|假设|\b(?:speculat\w*|mechanism|in general)\b',question,re.I))
    if explanation or not focus or not question_plan(question)['asks_reason'] or not personal_recall_intent(question):
        return model_step
    query=registry.get('memory.query')
    pending=bool(query and query.availability=='available')
    full_requested=set()
    scope={'role':'system','content':
        '[Host recall answer scope]\nAnswer only the requested object and period with the recorded reason. '
        'Do not add general properties or mechanisms, infer excluded motives, discuss other versions, '
        'or ask to save again. If the requested reason is present, give it directly. '
        'Otherwise state only the bounded evidence gap. This applies to recall, not an explicitly requested general explanation.'}
    def step(context,capabilities):
        nonlocal pending
        allowed={c.get('name') for c in capabilities}
        if pending:
            pending=False
            # This is a host read, not a claimed model decision. It uses the
            # same registry, per-tool limits, cost/latency budget and journal.
            if 'memory.query' in allowed:
                return {'tool_calls':[{'id':'host_recall_query','name':'memory.query','arguments':{'query':focus}}]}
        if 'memory.read' in allowed:
            full_seen={row.get('id') for item in context if item.get('role')=='tool' and item.get('name')=='memory.read'
                       for row in (item.get('result') or {}).get('items',[])}
            incomplete=[row['id'] for item in context if item.get('role')=='tool' and item.get('name')=='memory.query'
                        for row in (item.get('result') or {}).get('items',[])
                        if row.get('text_truncated') and row.get('id') and row['id'] not in full_seen|full_requested]
            if incomplete:
                ids=list(dict.fromkeys(incomplete))[:10]
                full_requested.update(ids)
                return {'tool_calls':[{'id':'host_recall_full','name':'memory.read','arguments':{'memory_ids':ids}}]}
        visible=list(context)+[scope]
        return model_step(visible,capabilities) if len(inspect.signature(model_step).parameters)>=2 else model_step(visible)
    return step
