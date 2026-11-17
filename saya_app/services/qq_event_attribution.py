"""Readable event attribution. Account IDs bind identity; names are display DATA only."""
import re
import unicodedata


def clean_label(value):
    if not isinstance(value,str):return ''
    return ''.join(c for c in value if not unicodedata.category(c).startswith('C')).strip()[:32]


def source_label(message):
    return clean_label(getattr(message,'sender_name','') or getattr(message,'sender_nickname',''))


def author_ref(account,record=None,message=None):
    account=str(account or '')
    if not re.fullmatch(r'[1-9]\d{0,19}',account):return '未知发言者'
    label=clean_label((record or {}).get('speaker_label',''))
    if not label and message is not None and str(message.user_id)==account:label=source_label(message)
    return (label+'（QQ:'+account+'）') if label else 'QQ:'+account


def owner_ref(card,message=None):
    subject=card.get('subject')
    if not subject:return '历史公共记录'
    source=next((f for f in reversed(card.get('facts',[])) if str(f.get('speaker',''))==str(subject)),{})
    return author_ref(subject,source,message)


def title_author(card,message=None):
    """Old titles remain quotations; infer their author only with unambiguous evidence."""
    actor=card.get('title_speaker')
    if actor:return author_ref(actor,{'speaker_label':card.get('title_speaker_label','')},message)
    candidates=[c for c in card.get('contributions',[]) if card.get('title') and card['title'] in c.get('text','')]
    actors={c['actor'] for c in candidates}
    return author_ref(candidates[0]['actor'],candidates[0],message) if len(actors)==1 else '以各条发言者标注为准'


def participants(card,message=None):
    records={c['actor']:c for c in card.get('contributions',[]) if not c.get('superseded')}
    refs=[author_ref(actor,record,message) for actor,record in sorted(records.items())[:4]]
    return '、'.join(refs)+('等' if len(records)>4 else '') or '来源不可用'
