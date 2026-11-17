"""QQ-only presentation: retain canonical evidence, project a conversational reply."""

import re
from .history_output import strip_history_labels


def wants_sources(text):
    """Explicit requests can show links; merely discussing sources is not consent."""
    target = r'(?:参考来源|参考文献|出处|来源|原文|链接|网址|sources?|references?|citations?|urls?|links?)'
    for clause in re.split(r'[，,。；;\n]', str(text or '')):
        if not re.search(target, clause, re.I):
            continue
        if re.search(r'(?:不要|不用|无需|别|不必|不显示|不附|without|no)\s*.{0,8}' + target, clause, re.I):
            continue
        if (re.search(r'(?:给|发|附|列|带|显示|提供|想看|查看|看看|需要|include|show|provide|with)\s*.{0,14}' + target, clause, re.I)
                or re.fullmatch(r'\s*(?:请问)?\s*' + target + r'\s*(?:呢|在哪|在哪里|是什么|给我|发我|发来)?[?？]?\s*', clause, re.I)):
            return True
    return False


def wants_command_help(text):
    text = str(text or '').strip()
    if re.search(r'(?:不要|不用|别|无需).{0,8}(?:命令|指令|command)', text, re.I):
        return False
    return bool(re.search(r'(?<![\w/])/(?:continue|topics|more|help|settings|set|memories|memory-save|memory-ignore|remember|recall|search|group|groups|requests|approve|reject|reset|status|whoami|memory)\b', text, re.I)
                or re.search(r'(?:命令|指令|用法|操作方法|commands?)|(?:怎么|如何).{0,10}(?:续读|继续|确认记忆|保存记忆)', text, re.I))


def project_operation_hints(text, user_input=''):
    """Known application commands become prose unless help was requested.

    Not a slash/path scrubber: keep URLs, shell commands and unrelated code.
    """
    if wants_command_help(user_input):
        return text
    replacements = [
        (r'(?:continue|more|续读)(?:\s+[a-f0-9]{16})?', '接着说'),
        (r'memory-save(?:\s+[A-Za-z0-9_-]+)?', '确认这条记忆'),
        (r'memory-ignore(?:\s+[A-Za-z0-9_-]+)?', '忽略这条记忆'),
        (r'set(?:\s+[A-Za-z_]+(?:\s+[A-Za-z0-9_.-]+)?)?', '调整相关设置'),
        (r'(?:topics|未完话题)', '查看没说完的话题'),
        (r'memories', '查看待确认记忆'),
        (r'(?:help|settings|whoami|owner|reset|status|memory|remember|recall|search|groups|requests|group|approve|reject)\b', '查看相关操作说明'),
    ]
    for pattern, prose in replacements:
        # Remove a backtick wrapper too when it contains only an app command.
        text = re.sub(r'(?<![\w/])`?/(?:'+pattern+r')(?![\w-])`?', prose, text, flags=re.I)
    return text


def reply_guidance(show_sources=False, *, natural=False, show_commands=False):
    return (
        '\n[System: QQ delivery style]\n'
        'Preserve the existing character, personality, relationship, owner identity and memory rules. '
        'Only adapt delivery to a QQ conversation. Reply directly and naturally in the user\'s language. '
        'Do not announce receipt, thinking, searching or completion before answering. '
        'Do not explain account verification, name mappings or your character instructions during ordinary conversation. '
        'Default to 1-3 short sentences, roughly 80-200 Chinese characters as a soft target. '
        'For news, pick 2-3 useful highlights. Expand when the user explicitly requests details, steps, code or a full explanation; '
        'never omit essential uncertainty or conditions just to meet the soft target. '
        'Avoid report headings, task-completion summaries, capability inventories and routine closing questions. '
        'Do not force emojis, cute language or an owner salutation; follow the existing character. '
        'QQ has no 3D avatar playback: do not produce body-animation tags. '
        'Mood-state labels, thinking, system instructions and tool metadata are internal, never reader-facing prose. '
        + ('Use ordinary blank lines between 2-3 complete ideas when appropriate, even for medium-length replies. '
           'Each paragraph can become one QQ message. Merge tiny fragments; do not pad, repeat yourself or add special separator tags. '
           if natural else '')
        + ('The user explicitly requested operation help; exact relevant application commands may be shown. '
           if show_commands else 'Do not proactively show application slash commands, result IDs or operation instructions. '
           'Keep real save/failure/uncertainty receipts in natural language. Only show exact commands when the user asks for help. ')
        +
        'Use web evidence and memory internally. Do not narrate backend provider differences, safety-filter settings or tool plumbing. '
        'Be honest when research fails or evidence is insufficient; never pretend a search or a memory write succeeded. '
        'Retain evidence citation numbers inline for server-side validation; the QQ adapter controls their display. '
        + ('The user requested sources: provide the relevant verified links.\n' if show_sources else
           'Do not write a separate references/source list or paste source URLs unless needed to answer an explicit URL request.\n')
    )


_INFORMATIONAL = {'search_safe_search_unavailable', 'search_region_hint_unavailable', 'context_limited'}
_LEGACY_INFORMATIONAL = {
    'DeepSeek不提供与Brave等价的安全搜索筛选；当前未承诺内容过滤。',
    'DeepSeek搜索不支持Brave的地区参数，已使用支持的语言/时间条件。',
    '网页资料过长，已按上下文裁剪。',
}
_SEARCH_FAILURE = {'missing_key', 'search_failed', 'search_usage_limit_exceeded', 'search_insufficient_balance',
                   'search_auth_error', 'search_rate_limited', 'search_network_error', 'search_query_too_long'}


def warning_note(data):
    code = str(data.get('code') or '')
    message = str(data.get('message') or '')
    if code in _INFORMATIONAL or message in _LEGACY_INFORMATIONAL:
        return ''
    if code in _SEARCH_FAILURE:
        return '这次联网查询没成功，暂时没法核实最新消息。'
    if code == 'search_unsupported_safe_search':
        return '当前搜索条件不受支持，这次没有发起检索。'
    if code in {'partial_read', 'no_primary_source'}:
        return '这次只查到部分资料，细节还没完全核实。'
    if message == '部分网络工具未完成，请核对搜索密钥及网页可访问性；当前回答以已获取资料为准。':
        return '这次只查到部分资料，细节还没完全核实。'
    return message or '这次查资料遇到了问题，回答只能以已获取的信息为准。'


_PROTECTED = re.compile(r'(```[\s\S]*?(?:```|$)|~~~[\s\S]*?(?:~~~|$)|`[^`\n]+`|\$\$[\s\S]*?\$\$|\$[^$\n]+\$)')
# Limited to this application's protocol, including the observed misspelling.
# Arbitrary bracketed prose and source IDs are not control tags.
_CONTROL = r'(?:MOTION|MOOD(?:\s*_\s*(?:SAYA|USER))?|MOD(?:AL)?\s*_\s*SAYA|MC)'
_CONTROL_TAG = re.compile(r'[\[［]\s*' + _CONTROL + r'\s*[:：][^\]］\r\n]{0,240}[\]］]', re.I)
_UNFINISHED_CONTROL = re.compile(r'[\[［]\s*' + _CONTROL + r'\s*[:：][A-Za-z_ \t-]{0,80}(?=\r?$)', re.I | re.M)
# A reserved mood namespace with an exact supported value also catches bounded
# model spelling drift (e.g. MODEL_SAYA), without treating arbitrary brackets,
# identifiers or non-mood values as protocol. Code/math still remain literal.
_MOOD_VALUE = r'(?:neutral|happy|excited|sad|stressed|depressed|angry|worried|tired|curious|affectionate|confident)'
_MOOD_NAMESPACE = re.compile(r'[\[［]\s*M[A-Z_ \t-]{0,24}(?:SAYA|USER)\s*[:：]\s*'+_MOOD_VALUE+r'\s*[\]］]',re.I)
_THINKING = re.compile(r'<think\b[^>]*>[\s\S]*?(?:</think\s*>|$)|</think\s*>', re.I)
_LITERAL_OR_THINKING = re.compile(_PROTECTED.pattern + '|' + _THINKING.pattern, re.I)
_MOOD_INSTRUCTION_TAIL = re.compile(
    r'\r?\n[ \t]*→[ \t]*(?:neutral|happy|excited|sad|stressed|depressed|angry|worried|tired|curious|affectionate|confident)'
    r'[ \t]*\r?\n(?:[ \t]*\r?\n)*[ \t]*Use one label:[ \t]*\Z', re.I)


def clean_control_output(text):
    """QQ visible text only; literal code/math examples remain intact.

    This is a protocol filter, not a claim to detect arbitrary prompt disclosure.
    Apply before persistence/splitting and again when sending older cached parts.
    """
    text = strip_history_labels(str(text or ''))
    parts, start = [], 0
    def prose(value):
        return _UNFINISHED_CONTROL.sub('', _MOOD_NAMESPACE.sub('', _CONTROL_TAG.sub('', value)))
    for match in _LITERAL_OR_THINKING.finditer(text):
        parts.append(prose(text[start:match.start()]))
        # A thinking block beginning first consumes its whole contents, even
        # if it includes fenced code. A literal beginning first stays literal.
        parts.append(match.group(1) or '')
        start = match.end()
    # Only the observed paired metadata tail, outside any literal and at the
    # end of the answer. Ordinary labels, quotations and code remain data.
    parts.append(prose(_MOOD_INSTRUCTION_TAIL.sub('', text[start:])))
    return ''.join(parts).strip()


def without_web_motion_instructions(prompt):
    """Remove only the built-in animation suffix, retaining its persona prefix."""
    for marker in ('IMPORTANT: At the very beginning of your response, you MUST choose exactly one chat MOTION',
                   '重要：在回复的开头，你必须将当前的动作/情感归类为以下标签之一：'):
        prompt = prompt.split(marker, 1)[0]
    return prompt


def clean_assistant_history(history):
    """Ephemeral input projection; leave user turns and original files intact."""
    return ['Saya: ' + clean_control_output(value[len('Saya:'):])
            if isinstance(value, str) and value.startswith('Saya:') else value for value in history]
_CITATION = re.compile(r'\[(\d+(?:\s*[,，–—-]\s*\d+)*)\]')
_SOURCE_HEADER = re.compile(r'^\s*(?:#{1,6}\s*)?(?:参考来源|参考资料|参考文献|信息来源|来源|sources?|references?)\s*[:：]?\s*$', re.I)


def project_answer(answer, sources, source_ids, *, show_sources=False, user_input=''):
    """Project QQ controls and known source markup without changing evidence.

    Literal code/math, unknown numbers/URLs, array/index syntax and user URLs
    are retained rather than aggressively deleting arbitrary brackets or links.
    """
    answer = clean_control_output(answer)
    answer = project_operation_hints(answer, user_input)
    if show_sources:
        return answer
    ids = {str(value) for value in source_ids}
    titles = set(sources.values())

    def prose(text):
        lines = text.splitlines(keepends=True)
        for index, line in enumerate(lines):
            if not _SOURCE_HEADER.fullmatch(line.strip()):
                continue
            tail = [value.strip() for value in lines[index + 1:] if value.strip()]
            if tail and all(any(url in value for url in sources) or re.sub(r'^[-*\d.\[\] ]+', '', value) in titles for value in tail):
                text = ''.join(lines[:index]).rstrip()
                break
        # Strip known source links only; user-supplied URLs and unrelated links
        # are part of the actual question/answer, not automatic references.
        for url in sources:
            if url in user_input:
                continue
            text = re.sub(r'\[([^\]\n]+)\]\(' + re.escape(url) + r'\)', r'\1', text)
            text = text.replace(url, '')

        def citation(match):
            numbers = set()
            for group in re.split(r'[,，]', match.group(1)):
                bounds = re.split(r'[-–—]', group)
                if len(bounds) == 2:
                    first, last = map(int, bounds)
                    if not 0 < first <= last <= first + 50:
                        return match.group(0)
                    numbers.update(str(value) for value in range(first, last + 1))
                else:
                    numbers.add(group.strip())
            if not numbers.issubset(ids):
                return match.group(0)
            before = text[max(0, match.start() - 24):match.start()]
            after = text[match.end():match.end() + 5]
            # Be conservative around numeric examples and indexing.
            if (re.search(r'(?:数组|列表|索引|array|index|list)\s*[:：=]?\s*$', before, re.I)
                    or re.search(r'[A-Za-z0-9_=\[(]$', before)
                    or re.match(r'\s*(?:项|条|位|个|章|节|\]|[,，]\s*[\d\[\]])', after)):
                return match.group(0)
            return ''
        text = _CITATION.sub(citation, text)
        return re.sub(r'[ \t]+([，。！？；：,.!?;:])', r'\1', text)

    parts = _PROTECTED.split(str(answer or ''))
    return ''.join(part if index % 2 else prose(part) for index, part in enumerate(parts)).strip()
