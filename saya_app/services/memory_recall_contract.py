"""Bounded read evidence: preserve distinctions without inventing recall coverage."""
import re
from datetime import datetime

_REASON=re.compile(r'为什么|为何|原因|理由|缘由|主要考虑|怎么考虑|看中|出于什么|\bwhy\b|\breason\b',re.I)
_PAST=re.compile(r'之前|以前|当时|当初|过去|上次|曾经|那时|那阵子|当年|\bprevious|\bearlier|\bused to\b',re.I)
_CURRENT=re.compile(r'现在|目前|如今|最新|\bcurrent|\bnow\b',re.I)
_PERSONAL=re.compile(r'我|我们|记得|记忆|\bmy\b|\bi\b|\bremember\b',re.I)
_CAUSE=re.compile(r'因为|由于|理由[是：:]|原因[是：:]|缘由[是：:]|\bbecause\b',re.I)
_PREFIX=re.compile(r'^(?:(?:请问|你还记得|你记得|我们|我|那次|之前|以前|当时|当初|现在|目前|选择|采用|使用|选用|决定|给|在|为|把)(?:的)?)+')
_LOOKUP_PREFIX = re.compile(r'^(?:我想|请)(?:核对|查看|查询|了解|回顾)(?:一下)?\s*')
_CALENDAR_YEAR = re.compile(r'(?<!\d)(20\d{2})\s*年')
_PREVIOUS_VERSION = re.compile(r'上一版|前一版')


def question_plan(query):
    text=str(query or '')[:2000]
    clean=re.sub(r'为什么|为何|原因是什么|理由是什么',' ',_LOOKUP_PREFIX.sub('', text))
    anchor=None
    match=re.search(r'([\w\u3400-\u9fff-]{2,24}?(?:计划|项目|方案|实验|试验|活动))',clean)
    if match:
        candidate=_PREFIX.sub('',match[1])
        if 4<=len(candidate)<=24:anchor=candidate
    return {'asks_reason':bool(_REASON.search(text)),'asks_past':bool(_PAST.search(text)),
        'asks_current':bool(_CURRENT.search(text)),'personal_history_request':bool(_PERSONAL.search(text)),
        'literal_topic_anchor':anchor}


def single_literal_focus(query):
    text=str(query or '')[:2000]
    if re.search(r'[\n\r"“”「」『』]|不是|并非|不要',text):
        return None
    # Multiple named objects must retain their comparison material.
    if len(re.findall(r'(?:计划|项目|方案|实验|试验|活动)',text)) != 1:
        return None
    return question_plan(text)['literal_topic_anchor']


def explicit_self_choice(query):
    """Recognize a direct self-choice subject, not 'I want to know about X'."""
    text=str(query or '')[:2000]
    if re.search(r'[\n\r"“”「」『』]|我(?:在)?想|我听|我问|他说|她说',text):
        return False
    decision=bool(re.search(r'选|决定|采用|使用|用的|\b(?:choose|chose|chosen|picked|decided)\b',text,re.I))
    chinese=bool(re.search(r'我(?:自己)?(?:选|决定|采用|使用|在|给|为|之前|当时|当初|曾经|现在|目前|那次|那时|已经)',text))
    english=bool(re.search(r'\bdid I (?:choose|pick|decide)|\bI (?:chose|have chosen|picked|decided)',text,re.I))
    return decision and (chinese or english)


def explicit_self_recall(query):
    if explicit_self_choice(query):
        return True
    text = str(query or '')[:2000]
    focus = single_literal_focus(text)
    # A direct question about the speaker's records has a subject even when
    # it asks about an omitted factor rather than naming a decision verb.
    return bool(focus and re.match(
        r'^(?:请问)?我(?:之前|以前|当时|当初|曾经)?(?:在|对|关于)?' + re.escape(focus) +
        r'的记录\s*(?:有没有|是否|有无|可曾|曾否)', text))


def named_period(query):
    """Recognize a single named object's explicit period without inferring dates."""
    text = str(query or '')[:2000]
    focus = single_literal_focus(text)
    if not focus or re.search(r'分别|比较|对比|回到|说回|明年|后年|\bcompare\b|\bback to\b', text, re.I):
        return None
    years = set(_CALENDAR_YEAR.findall(text))
    current = bool(_CURRENT.search(text))
    if current and (years or _PAST.search(text) or _PREVIOUS_VERSION.search(text)):
        return None
    if current:
        return focus, 'current'
    if len(years) == 1:
        year = years.pop()
        if int(year) < datetime.now().year and not _PREVIOUS_VERSION.search(text):
            return focus, year
    if not years and _PREVIOUS_VERSION.search(text):
        return focus, 'previous_version'
    return None


def matches_named_period(text, target):
    """Filter already eligible records by their explicit statements of time.

    The text describes the subject's period. It never changes the storage
    lifecycle, validity, or observation date of that record.
    """
    if target is None:
        return True
    focus, period = target
    clauses = [clause for clause in re.split(r'[。！？；;\n]', text) if focus in clause]
    for clause in clauses:
        years = set(_CALENDAR_YEAR.findall(clause))
        current = bool(_CURRENT.search(clause))
        if period == 'current':
            if current or (not _PREVIOUS_VERSION.search(clause) and
                           not _PAST.search(clause) and
                           not any(int(year) < datetime.now().year for year in years)):
                return True
        elif period == 'previous_version':
            if not current and _PREVIOUS_VERSION.search(clause):
                return True
        elif years == {period} and not current:
            return True
    return False


def personal_recall_intent(query):
    """A direct question about one's existing choice can require recall.

    General explanations, quoted examples and advice do not opt in merely
    because they contain 'I'. The existing explicit-history gate is separate.
    """
    text = str(query or '')[:2000]
    # A named object's recorded state does not require a first-person pronoun.
    # Keep this narrow: questions beginning with that literal object, rather
    # than general advice, quoted examples or new assertions about it.
    focus = single_literal_focus(text)
    if focus and re.match(r'^(?:请问)?' + re.escape(focus), text):
        question = bool(re.search(r'[？?]|什么|哪个|哪种|为何|为什么|是否|有没有', text))
        confirmation = bool(re.search(r'(?:已经|是否已|有没有)(?:确认|证实)', text))
        recorded_state = bool(re.search(
            r'(?:使用|采用|选择|安排|负责|先做|先核对|用).{0,8}(?:什么|哪种|哪个|哪一)|是否仍|仍(?:使用|采用|用)', text))
        advice = bool(re.search(r'建议|推荐|为什么要|为何要|应该|\bshould\b|\bwould\b', text, re.I))
        if question and (not advice or confirmation) and (
                _REASON.search(text) or _CURRENT.search(text) or _PAST.search(text) or confirmation or recorded_state):
            return True
    if re.search(r'[\n\r"“”「」『』]|应该|建议|推荐|为什么要|为何要|\bshould\b|\bwould\b', text, re.I):
        return False
    plan = question_plan(text)
    if not plan['personal_history_request']:
        return False
    if plan['asks_reason'] and re.search(r'选|决定|采用|使用|\b(?:chose|choose|chosen|decided|picked)\b', text, re.I):
        return True
    has_period = plan['asks_current'] or plan['asks_past'] or bool(re.search(r'明年|后年|20\d{2}年|next year', text, re.I))
    asks_state = bool(re.search(r'什么|哪个|哪种|哪一|[？?]|\bwhat\b|\bwhich\b', text, re.I))
    return bool(plan['literal_topic_anchor'] and has_period and asks_state)


def project_item(raw):
    value=raw.to_dict() if hasattr(raw,'to_dict') else dict(raw)
    result={key:value.get(key) for key in ('id','canonical_text','kind','scope_type','scope_id',
        'subject','predicate','authority','lifecycle','revision','valid_from','valid_to',
        'last_confirmed_at','created_at','updated_at','supersedes_id','confidence')}
    if value.get('historical_source'):result['historical_source']=dict(value['historical_source'])
    return result


def legacy_item(memory,text=None):
    from .memory_versions import record_version
    return {'id':str(memory['id']),'canonical_text':str(text if text is not None else memory.get('content') or ''),
        'kind':memory.get('memory_layer') or memory.get('category') or 'semantic',
        'scope_type':'project' if memory.get('project_id') else 'global','scope_id':memory.get('project_id'),
        # Old unstructured text does not prove who a statement is about.
        'subject':'unstructured_legacy','predicate':None,'authority':'legacy_record',
        'lifecycle':'archived' if memory.get('is_archived') else 'superseded' if memory.get('superseded_by') else 'active',
        'revision':record_version(memory),'valid_from':None,'valid_to':memory.get('expires_at'),
        'last_confirmed_at':None,'created_at':memory.get('creation_time') or memory.get('date'),
        'updated_at':memory.get('last_updated'),'supersedes_id':None,'confidence':memory.get('confidence')}


def evidence_contract(query,items,*,status='ready'):
    plan=question_plan(query);rows=list(items)
    reason_ids=[]
    for row in rows:
        text=str(row.get('canonical_text') or '')
        if plan['literal_topic_anchor'] and plan['literal_topic_anchor'] not in text:continue
        if plan['personal_history_request'] and row.get('subject') not in {'user','unstructured_legacy'}:continue
        if _CAUSE.search(text):reason_ids.append(str(row.get('id')))
    unresolved = bool(plan['asks_reason'] and plan['literal_topic_anchor'] and rows and not any(
        plan['literal_topic_anchor'] in str(row.get('canonical_text') or '') for row in rows))
    return {'version':1,'retrieval_status':('read_unavailable' if status != 'ready' else
        'selected_currently_usable_material' if rows else 'no_match_in_current_read'),
        'coverage':'bounded_read_not_complete_history','question':plan,
        'reason_status':('object_reference_unresolved' if unresolved else
            'explicit_passages' if reason_ids else 'not_found_in_selected_material') if plan['asks_reason'] else 'not_requested',
        'reason_passage_ids':reason_ids,'selection_count':len(rows)}


def recall_rules(contract=None):
    rules = ('Recall evidence boundary: match subject/object/time. Do not list other people or topics unless asked to compare. Choice is no cause. '
        'No speculative reasons unless asked. Use recorded reasons; no unasked factors or context requests. '
        'One reason does not rule out others. Never borrow another person\'s reason. '
        'Missing here does not mean never saved or invalidate others. No unread search claims. '
        'No match is bounded; never assert Doctor never said it. A selected version does not prove no later updates exist. '
        'Never revive deleted/unavailable sources. Candidates unconfirmed. '
        'Never complete clipped words or reasons. State gaps. Do not discuss unasked periods. ')
    if contract is not None:
        rules += f"Read={contract['retrieval_status']}; reason={contract['reason_status']}."
    return rules + '\n'


def prompt_evidence_contract(query, items, selection):
    """Describe only the compiled material; retrieval and inclusion differ."""
    items = list(items)
    contract = evidence_contract(query, items)
    focus = single_literal_focus(query)
    contract['grounded_literal_focus'] = focus if focus and any(
        focus in str(item.get('canonical_text') or '') for item in items) else None
    contract['selection'] = dict(selection)
    limited = bool(selection.get('omitted_ids') or selection.get('clipped_ids'))
    contract['selection_status'] = 'omitted_or_clipped' if limited else 'bounded_selection'
    clipped = {identifier.removeprefix('memory:') for identifier in selection.get('clipped_ids', [])}
    incomplete_reasons = [identifier for identifier in contract['reason_passage_ids'] if identifier in clipped]
    contract['incomplete_reason_passage_ids'] = incomplete_reasons
    if incomplete_reasons and len(incomplete_reasons) == len(contract['reason_passage_ids']):
        contract['reason_status'] = 'partially_visible_passages'
    if selection.get('available_count', 0) and not contract['selection_count']:
        contract['retrieval_status'] = 'matched_material_omitted_from_prompt'
    return contract


def explicit_grounded_topic_shift(query, contract):
    """Select a history view for this turn; never edit stored conversation."""
    text=str(query or '')[:2000]
    if not contract.get('grounded_literal_focus') or re.search(r'[\n\r"“”「」『』]',text):
        return False
    return bool(re.search(r'换(?:一)?个话题|换话题|另一个话题|switch(?:ing)? (?:the )?topic',text,re.I))


def explicit_future_window(query, now=None):
    """Only resolve a single unquoted, affirmative future calendar year.

    This selects an interval; it never enables expired, blocked or old versions.
    Explicit caller filters take precedence at the read adapter.
    """
    from datetime import datetime
    text = str(query or '')[:2000]
    if re.search(r'[\n\r"“”「」『』]|不是|不要|并非|别查|not\b', text, re.I):
        return {}
    matches = re.findall(r'明年|后年|next year|20\d{2}年', text, re.I)
    if len(matches) != 1:
        return {}
    current = (now or datetime.now()).year
    token = matches[0].lower()
    year = current + (2 if token == '后年' else 1) if token in {'明年', '后年', 'next year'} else int(token[:4])
    if year <= current:
        return {}
    return {'time_from': f'{year}-01-01', 'time_to': f'{year}-12-31'}
