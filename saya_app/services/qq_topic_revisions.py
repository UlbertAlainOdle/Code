"""Invalidate sibling projections of an explicitly corrected authored source.

Run inside the successful replacement's SQLite transaction. Only evidence
identity travels across projections; replacement text and control state do not.
"""
import json


def invalidate(service,db,message,old_sources,*,primary,identifier):
    plan=getattr(message,'topic_choice',None)
    meta=getattr(message,'topic_metadata',{})
    replacement=meta.get('evidence_id')
    if (not plan or plan.get('revision_kind') not in {'change','correction'} or
            not replacement or meta.get('mode') in {'reported','hypothesis','question'}):return
    bot=service.unified.bot;actor=message.user_id;now=service.clock()
    changed={}
    for old in old_sources:
        evidence=old.get('evidence_id')
        if not evidence or evidence==replacement:continue
        root=db.execute('select * from qq_topic_evidence where id=? and bot=? and actor=? and expires>?',
            (evidence,bot,actor,now)).fetchone()
        if not root or root['source']!=old['source']:continue
        data=json.loads(root['data'])
        if data.get('quote')!=old.get('quote',old.get('text')):continue
        if data.get('superseded_by') not in (None,replacement):continue
        for link in data.get('links',[]):
            kind=link['kind'];card_id=link['id']
            if (kind,card_id)==(primary,identifier):continue
            if kind=='personal':
                row=db.execute('select revision,data from qq_episode_cards where id=? and scope=?',
                    (card_id,service.people.scope(actor))).fetchone()
            elif kind=='group':
                row=db.execute('select revision,data from qq_shared_events where id=? and bot=?',
                    (card_id,bot)).fetchone()
            else:continue
            if not row:continue
            card=json.loads(row['data'])
            if card['epoch']!=service.epoch():continue
            sources=card['facts'] if kind=='personal' else card['contributions']
            from .qq_topic_lifecycle import source_expiry,pending
            matches=[x for x in sources if x.get('evidence_id')==evidence and x['source']==root['source']
                and x.get('speaker',x.get('actor'))==actor and not x.get('superseded')
                and (x['expires'] if kind=='personal' else source_expiry(card,x))>now]
            if not matches:continue
            for x in matches:
                x.update(superseded=True,superseded_by_evidence=replacement)
                if kind=='personal':service.people._guard_source(db,actor,card_id,x,blocked=True)
            if kind=='group':
                if card.get('current_decision') in {x['id'] for x in matches}:
                    card.pop('current_decision',None)
                    if card.get('attention'):card['attention'].pop('dated_decision_until',None)
                if card.get('attention'):card['attention']['pending']=pending(card)
                # An unanchored title may itself be the obsolete assertion.
                if not card.get('topic_anchor') and card.get('title_speaker')==actor:
                    card['title']='群话题（原发言已更正）';card['title_unavailable']=True
            elif not any(x.get('topic_anchor')==card['title'] for x in sources):
                card['title']='本人话题（原发言已更正）'
            table='qq_episode_cards' if kind=='personal' else 'qq_shared_events'
            db.execute('update '+table+' set revision=revision+1,data=? where id=? and revision=?',
                (json.dumps(card,ensure_ascii=False),card_id,row['revision']))
            db.execute('delete from qq_topic_state_cache where bot=? and kind=? and card=?',(bot,kind,card_id))
            changed[(kind,card_id)]=row['revision']+1
        data.update(superseded_by=replacement,revision_version=1)
        db.execute('update qq_topic_evidence set data=? where id=?',(json.dumps(data,ensure_ascii=False),evidence))
    message.topic_invalidated_versions=changed
