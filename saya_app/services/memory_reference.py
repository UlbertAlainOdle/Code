"""Conservative recognition of unresolved chat-save references, not facts."""
import re

_REFERENCE=re.compile(r'^(?:(?:你|我|您)(?:们)?)?(?:(?:刚才|刚刚|上面|前面|之前|上次|上述|刚)(?:说过|说的|说|提到的|提过的|讲的|给出的|推荐的|建议的)?(?:的)?(?:这|那)?(?:一|两|几|[二三四1234])?(?:条|段|个|些|份)?(?:记忆|内容|话|观点|解释|建议|候选|东西|事情)?|(?:这|那|上一|前一)(?:一|两|几|[二三四1234])?(?:条|段|个|些)(?:记忆|内容|话|观点|解释|建议|候选|东西|事情)?)(?:都|全部)?$')
_SAVE=re.compile(r'^\s*(?:请|帮我)?\s*(?:记住|记一下|记下来|保存(?:到长期记忆)?)[：:，,\s]*(.*?)\s*[。！!]*$',re.S)
_SET=re.compile(r'^(?:(?:你|我|您)?(?:刚才|刚刚|上面|前面|之前)(?:说的|提到的|的)?)(?:这|那)?(?:两|[一二三四1234几])(?:条|段)(?:记忆|内容|话|观点|解释|候选|偏好|安排)?(?:都|全部)?$')
_POINTER=re.compile(r'(?:上文|前文|上述|刚才|刚刚|上面|前面|之前|先前|上一轮|前一轮|那(?:一)?(?:个|些|段|条|句|点|种|层)|这(?:一)?(?:个|些|段|条|句|点|种|层))|\b(?:above|previous|earlier|you (?:just )?said|that (?:idea|point|explanation))\b',re.I)
_COUNTED_SOURCE=re.compile(r'^(?:(?:你|我|助手|本人)?(?:刚才|刚刚|上面|前面|之前)(?:说的|说过的|提到的|的)?)(?:这|那)?[两二三四234](?:条|段)')
_OBJECT_SAVE=re.compile(r'^(?:请(?:你)?|帮我|麻烦(?:你)?)?\s*(?:把|将)(.+?)[，,]?\s*'
    r'(?:分别|各自|分开|单独|一一|都|全部)?\s*(?:保存|存入|存进|写入|记入)'
    r'(?:成|为|到|进|入)?\s*(?:[两二三四234]条)?(?:长期记忆|记忆条目|记忆)[。！!]*$',re.S)

def explicit_save_body(text):
    value=str(text or '').strip()
    match=re.fullmatch(r'(?:请(?:你)?|請|帮我|幫我|麻烦(?:你)?|你要|以后|以後)?\s*(?:只|仅|一定|必须|必須|务必|務必|千万(?:要)?|千萬)?\s*'
        r'(?:记住|記住|记一下|記一下|记下来|記下來|保存(?:到长期记忆)?|存一下)\s*(?:(?:这件事|這件事|这一点|這一點|一下)[：:，,\s]+)?[：:，,\s]*(.+)',value,re.S)
    if not match:match=re.fullmatch(r'(?:请(?:你)?|帮我|麻烦(?:你)?)?把(.+?)(?:都)?(?:记住|记下来|保存(?:到长期记忆)?|存一下)[。！!]*',value,re.S)
    if not match:
        match=_OBJECT_SAVE.fullmatch(value)
        if match and re.search(r'(?:不要|不用|无需|禁止|别)\s*$',match[1]):match=None
    if not match:match=re.fullmatch(r'(?:please\s+)?remember(?:\s+that|\s+this|\s*[:：,，])?\s+(.+)',value,re.I|re.S)
    if not match:match=re.fullmatch(r'(?:save|keep)\s+(?:this\s+)?(?:memory|note)\s*(?:[:：,，])?\s+(.+)',value,re.I|re.S)
    if not match:match=re.fullmatch(r'/remember\s+(.+)',value,re.S)
    return match[1].strip() if match else None

def contextual_save(text):
    body=explicit_save_body(text)
    return bool(body and _POINTER.search(body))

def source_author(text):
    body=explicit_save_body(text) or ''
    match=re.match(r'^(你|助手|我|本人)(?:刚|上文|上面|前文|前面|之前|先前)',body)
    if not match:match=re.match(r'^(?:刚刚|刚才|上文|前面)(你|助手|我|本人)',body)
    return ('助手' if match[1] in {'你','助手'} else '用户') if match else None

def _loose_reference(value):
    if value.startswith(('用户选择保存本次','用户明确选择保存的陈述：')):return False
    if not _POINTER.search(value[:80]):return False
    # A complete assertion retains its old literal-save contract. The QQ
    # semantic gate still checks contextual assertions before any chat write.
    assertion=re.search(r'(?:是|叫作|叫|名字为|标题为)\s*[《“「"]?(.+?)\s*[》”」"]?[。.!！]*$',value)
    if assertion and len(assertion[1])>=2 and (not _POINTER.search(value[:assertion.start()]) or not _POINTER.search(assertion[1])):return False
    return True

def reference_parts(text):
    value=str(text or '').strip()
    match=re.fullmatch(r'(?:请|帮我)?(?:只|仅)?(?:记住|保存)(.*?)(?:中(?:的)?|里(?:的)?)\s*[“「"](.{1,2000})[”」"]\s*[。！!]*',value,re.S)
    if match and unresolved_reference(match[1]):return {'kind':'fragment','reference':match[1],'fragment':match[2]}
    match=re.fullmatch(r'(?:请)?把(.+?)(?:都)?(?:记住|记下来|保存(?:到长期记忆)?)[。！!]*',value,re.S)
    if match and unresolved_reference(match[1]):
        fragment=re.fullmatch(r'(.+?)(?:中(?:的)?|里(?:的)?)\s*[“「"](.{1,2000})[”」"]',match[1],re.S)
        if fragment:return {'kind':'fragment','reference':fragment[1],'fragment':fragment[2]}
        return {'kind':'set','reference':match[1]}
    return None

def unresolved_reference(content):
    value=str(content or '').strip().rstrip('。！!').strip()
    fragment=re.fullmatch(r'(.+?)(?:中(?:的)?|里(?:的)?)\s*[“「"](.{1,2000})[”」"]',value,re.S)
    return bool(_REFERENCE.fullmatch(value) or _SET.fullmatch(value) or _loose_reference(value)
        or re.fullmatch(r'第?(?:[1-9]\d?|[一二三四五六七八九十]+)(?:条|段)',value)
        or (fragment and unresolved_reference(fragment[1]))
        or re.fullmatch(r'(?:这|那)(?:条|个)?候选(?:记忆)?',value)
        or re.fullmatch(r'(?:what you (?:just )?said|(?:the |that |this )?(?:above|previous) (?:memory|message|explanation))',value,re.I))

def save_reference(text):
    parts=reference_parts(text)
    if parts:return parts['kind']
    match=_SAVE.fullmatch(str(text or '').strip())
    if not match:
        body=explicit_save_body(text)
        return ('set' if _COUNTED_SOURCE.match(body) else 'semantic') if body and unresolved_reference(body) else None
    if not match or not unresolved_reference(match[1]):return None
    if re.fullmatch(r'第?(?:[1-9]\d?|[一二三四五六七八九十]+)(?:条|段)',match[1]):return 'selection'
    if _SET.fullmatch(match[1]) or _COUNTED_SOURCE.match(match[1]) or re.search(r'(?:都|全部)$',match[1]):return 'set'
    return 'candidate' if '候选' in match[1] else 'ambiguous'

def reference_count(text):
    match=re.search(r'([两二三四234])(?:条|段)',str(text))
    return {'两':2,'二':2,'三':3,'四':4,'2':2,'3':3,'4':4}[match[1]] if match else None

def readonly_memory_interlude(text):
    # Only known read operations; changing a policy or target remains a turn.
    return bool(re.fullmatch(r'/events(?:\s+(?:self|stopped|all)|\s+(?:show|why|user)\s+\S+|\s+group(?:\s+stopped|\s+(?:show|why)\s+\S+)?)?',text)
        or re.fullmatch(r'/memory\s+(?:list|candidates|show\s+\S+|history\s+\S+|result\s+\S+)',text)
        or text in {'你有哪些长期记忆','你记住了我什么','展开刚记的那条','刚才记下了什么'})
