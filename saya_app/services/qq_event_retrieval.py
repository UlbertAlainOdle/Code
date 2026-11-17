"""Content-aware selection over effective, attributed evidence, including old facts."""
import re


def terms(text):
    text=re.sub(r'目前|当前|现在|之前|那个|这个|事件|话题|计划|请问|怎么处理|怎么样|如何|怎么|什么|是否|谁负责|分别|还有|告诉我|了吗',' ',text)
    out=set(re.findall(r'[A-Za-z0-9_]{3,}',text.lower()))
    for part in re.findall(r'[\u3400-\u9fff]{2,}',text):
        out.update(part[i:i+n] for n in range(2,min(5,len(part))+1) for i in range(len(part)-n+1))
    return out


def score(text,query):
    body=str(text).lower()
    return sum(len(t)**2 for t in terms(query) if t in body)


def card_score(card,query):
    facts=card.get('contributions',card.get('facts',[]))
    return score(card.get('title',''),query)*2+max((score(f.get('text',f.get('quote',''))+' '+str(f.get('speaker_label',''))+' '+str(f.get('actor',f.get('speaker',''))),query)
        for f in facts if not f.get('superseded')),default=0)


def evidence_order(facts,query):
    from .memory_recall_contract import named_period
    period=named_period(re.sub(r'^\s*(?:再)?(?:回到|说回)\s*','',query))
    # Repeated questions are not updated progress. For an explicit current
    # personal-status read, reserve the newest direct statement before lexical
    # similarity can crowd its full conditions out of the existing budget.
    direct=[f for f in facts if 'speaker' in f and not f.get('superseded') and
        f.get('mode') not in {'question','hypothesis'}] if period and period[1]=='current' else []
    latest=max(direct,key=lambda f:f.get('at',0),default=None)
    return sorted(facts,key=lambda f:(f is latest,bool(latest is not None and f in direct),score(f.get('text',f.get('quote','')),query),
        f.get('kind',f.get('mode')) in {'decision','objection','commitment','withdrawal'},f.get('at',0)),reverse=True)
