"""Conservative host topic hints for direct questions about earlier conversation."""
import re


def recall_topic(text):
    value=str(text or '').strip().rstrip('？?。！!').strip()
    if not 1<=len(value)<=200 or re.search(r'[\n`\[\]{}<>/\\:："“”‘’「」『』]',value):return None
    when=r'(?:最近|之前|以前|上次|曾经|这几天|这段时间)'
    intro=r'(?:(?:你)?(?:还)?(?:记得|记不记得))?'
    suffix=r'(?:的)?(?:事情|话题|内容|情况|信息|事)(?:吗|呢)?'
    patterns=[
        intro+r'(?:'+when+r'[，,]?)?(?:我|我们)(?:'+when+r')?(?:说|聊|谈|提)(?:了|过|到|起){0,3}(?:什么|哪些)?(?:关于|有关)(.+?)'+suffix,
        intro+r'(?:'+when+r')?(?:我|我们)(?:关于|有关)(.+?)(?:说|聊|谈|提)(?:了|过|到|起){0,3}(?:什么|哪些)(?:吗|呢)?',
        r'(?:你)?(?:还)?(?:记得|记不记得)(?:我|我们)?(?:'+when+r')?(?:说过|提过|聊过)?(?:关于|有关)(.+?)'+suffix,
        r'what did (?:i|we) (?:say|tell you|mention) about (.+?)(?: recently| before)?',
    ]
    for pattern in patterns:
        match=re.fullmatch(pattern,value,re.IGNORECASE)
        if not match:continue
        topic=match.group(1).strip()
        if (2<=len(topic)<=48 and re.fullmatch(r'[\w\u3400-\u9fff ·-]+',topic)
                and topic not in {'事情','话题','内容','信息','记忆','最近','以前','聊天','东西'}
                and not re.search(r'^(?:不|别|除|只|开启|关闭|删除|执行)|(?:或者|还是)',topic)):
            return topic
    return None
