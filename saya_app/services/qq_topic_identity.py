"""Source-verified topic labels; lexical similarity alone never authorizes a merge."""
import re
import json


def source_anchor(text,start,quote,proposed=None):
    if isinstance(proposed,str) and 4<=len(proposed)<=60 and proposed in text:
        return proposed
    # A short explicit subject before a colon is useful even if the classifier
    # selected only the first-person clause after it. Do not inherit it across
    # a new sentence/semicolon or treat a management verb as the topic itself.
    anchors=list(re.finditer(r'(?:^|[；;\n])\s*([^：:。！？!?；;\n]{4,60})[：:]',text))
    for match in reversed(anchors):
        name=match[1].strip()
        if re.match(r'请|帮我|记住|保存|补充|更正|确认',name):continue
        if match.end()<=start and not re.search(r'[；;。！？!?\n]',text[match.end():start]):return name
        if start<=match.start() and name in quote:return name
    return ''


def new_occurrence_choice(choice, occurrence_quote, text):
    if not isinstance(occurrence_quote, str) or not occurrence_quote or occurrence_quote not in text or not re.search(
            r'再(?:办|组织|来)|新一(?:次|轮)|(?:下周|下次).{0,8}(?:办|组织|再|活动|聚会)|第.{1,5}(?:次|期)|另一次', occurrence_quote):
        raise ValueError('new_occurrence_requires_source')
    if choice.get('supersedes'):
        raise ValueError('new_occurrence_cannot_rewrite')
    return {**choice, 'action': 'create', 'event_id': None}


def private_dialogue_clause(text, start, quote):
    delimiters = '；;\n。！？!?'
    left = max(text.rfind(mark, 0, start) for mark in delimiters) + 1
    end = start + len(quote.rstrip(delimiters))
    tail = re.split(r'[；;\n。！？!?]', text[end:], maxsplit=1)[0]
    return text[left:end] + tail


def private_dialogue_binding(choice, matches, relation, occurrence_quote, clause):
    if relation == 'new_occurrence':
        return new_occurrence_choice(choice, occurrence_quote, clause)
    if relation == 'uncertain' or len(matches) > 1:
        explicit = matches.intersection(re.findall(r'(?<![A-Za-z0-9])[a-f0-9]{16}(?![A-Za-z0-9])', clause))
        if choice['action'] != 'update' or explicit != {choice.get('event_id')}:
            raise ValueError('ambiguous_dialogue_topic')
        return choice
    if not matches:
        return choice
    identifier = next(iter(matches))
    if choice.get('supersedes') and choice.get('event_id') != identifier:
        raise ValueError('topic_revision_target_mismatch')
    return {**choice, 'action': 'update', 'event_id': identifier}


def group_binding(anchor,choice,candidates,relation,occurrence_quote,text):
    if not anchor:return choice
    if relation not in {'same_topic','new_topic','new_occurrence','uncertain'}:raise ValueError('invalid_group_relation')
    if relation=='new_occurrence':
        return new_occurrence_choice(choice, occurrence_quote, text)
    matches=[c for c in candidates if (c.get('topic_anchor')==anchor if c.get('topic_anchor') else anchor in c['title'])]
    if not matches:
        if choice.get('action')=='update':raise ValueError('topic_target_mismatch')
        return choice
    if len(matches)!=1 or relation=='uncertain':raise ValueError('ambiguous_group_topic')
    target=matches[0]
    if choice.get('action')=='update' and choice.get('event_id')==target['id']:return choice
    if choice.get('supersedes'):raise ValueError('topic_revision_target_mismatch')
    # Same explicit subject, same group, one live authorized candidate: append
    # a new speaker's contribution rather than creating a speaker-specific card.
    return {**choice,'action':'update','event_id':target['id'],'supersedes':[]}


def repair_split(db,*,bot,group,source,target,source_revision,target_revision,anchor,source_text,source_event,now,epoch,limit):
    """Narrow maintenance repair of one proven split contribution, inside caller's transaction.

    Not a chat action or a general merge. Original quote/author/IDs/TTL survive;
    controls, decisions, unknown provenance and concurrent updates reject repair.
    """
    if source==target:raise ValueError('same_topic_card')
    rows={r['id']:r for r in db.execute('select * from qq_shared_events where bot=? and grp=? and id in (?,?)',(bot,group,source,target))}
    if set(rows)!={source,target}:raise ValueError('repair_cards_missing')
    old=json.loads(rows[source]['data']);dest=json.loads(rows[target]['data'])
    if rows[source]['revision']!=source_revision or rows[target]['revision']!=target_revision:raise ValueError('repair_version_changed')
    if (old.get('epoch')!=epoch or dest.get('epoch')!=epoch or old['state'] not in {'active','cooling'} or dest['state'] not in {'active','cooling'}
            or old.get('coordinator') or old.get('current_decision') or len(old['contributions'])!=1):raise ValueError('repair_scope_not_safe')
    if db.execute('select 1 from qq_shared_event_stops where bot=? and card in (?,?)',(bot,source,target)).fetchone():raise ValueError('repair_stopped')
    claim=old['contributions'][0]
    if claim['source']!=source_event or claim.get('superseded') or claim['expires']<=now:raise ValueError('repair_source_unavailable')
    span=claim.get('source_span',[])
    if (len(span)!=2 or any(type(x) is not int for x in span) or source_text[span[0]:span[1]]!=claim['text']
            or source_anchor(source_text,span[0],claim['text'])!=anchor):raise ValueError('repair_source_anchor_mismatch')
    evidence=db.execute('select * from qq_topic_evidence where id=? and bot=? and grp=?',(claim.get('evidence_id'),bot,group)).fetchone()
    operation=db.execute('select actor,state,data from qq_topic_routes where event=? and bot=?',(source_event,bot)).fetchone()
    if (not evidence or evidence['actor']!=claim['actor'] or evidence['source']!=source_event or evidence['expires']<=now
            or not operation or operation['actor']!=claim['actor'] or operation['state'] not in {'committed','partial'}):raise ValueError('repair_provenance_mismatch')
    if json.loads(evidence['data']).get('quote')!=claim['text']:raise ValueError('repair_quote_mismatch')
    if not any(x.get('target')=='group' and x.get('id')==source and x.get('status')=='committed' for x in json.loads(operation['data']).get('results',[])):
        raise ValueError('repair_operation_mismatch')
    if not (dest.get('topic_anchor')==anchor or not dest.get('topic_anchor') and anchor in dest['title']):raise ValueError('repair_target_mismatch')
    if len(dest['contributions'])+1>limit or any(x['id']==claim['id'] for x in dest['contributions']):raise ValueError('repair_capacity_or_duplicate')
    if db.execute('select count(*) from qq_topic_aliases').fetchone()[0]>=16000:raise ValueError('repair_alias_capacity')
    dest['contributions'].append(claim);dest['topic_anchor']=anchor
    dest['contributions'].sort(key=lambda c:(c['at'],c['id']))
    dest['updated']=max(dest.get('updated',0),old.get('updated',0))
    db.execute('update qq_shared_events set revision=revision+1,data=? where id=? and revision=?',
        (json.dumps(dest,ensure_ascii=False),target,target_revision))
    db.execute('insert into qq_topic_aliases values(?,?,?,?,?,?)',(bot,group,source,target,claim['expires'],now))
    data=json.loads(evidence['data'])
    for link in data.get('links',[]):
        if link['kind']=='group' and link['id']==source:link.update(id=target,previous_id=source)
    data['topic_anchor']=anchor
    db.execute('update qq_topic_evidence set data=? where id=?',(json.dumps(data,ensure_ascii=False),evidence['id']))
    db.execute('update qq_shared_event_operations set card=?,revision=? where card=?',(target,target_revision+1,source))
    db.execute('update qq_shared_event_focus set card=? where bot=? and grp=? and card=?',(target,bot,group,source))
    db.execute('delete from qq_shared_events where id=? and revision=?',(source,source_revision))
    return {'moved_contribution':claim['id'],'target_revision':target_revision+1,'old_ID_alias':True}
