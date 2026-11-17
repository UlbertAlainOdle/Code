"""Hide prompt-only history labels; preserve literal examples and timestamps."""
import json
import re

_MARKER = '[History sent_at='
_LABEL = re.compile(
    r'\[History sent_at=(?:unknown|\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}'
    r'(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})); timezone=Asia/Shanghai\]'
)
_TAILS = ['unknown; timezone=Asia/Shanghai]'] + [
    '####-##-##T##:##:##' + fraction + offset + '; timezone=Asia/Shanghai]'
    for fraction in [''] + ['.' + '#' * n for n in range(1, 7)]
    for offset in ['Z', '+##:##', '-##:##']
]


def _possible_owned_label(value):
    if not value.startswith(_MARKER):
        return False
    tail = value[len(_MARKER):]
    return any(len(tail) <= len(pattern) and all(
        char in '0123456789' if expected == '#' else char == expected
        for char, expected in zip(tail, pattern)) for pattern in _TAILS)


class HistoryLabelStream:
    """Bounded prefix buffer; ordinary prose does not wait for a whole line."""
    def __init__(self):
        self.pending = ''
        self.probe = ''
        self.candidate = True
        self.fence = None
        self.math = False

    def _newline(self):
        value = self.probe.rstrip('\r\n')
        opening = re.match(r'^ {0,3}(`{3,}|~{3,})', value)
        if self.fence:
            marker, width = self.fence
            if re.fullmatch(r' {0,3}' + re.escape(marker) + '{' + str(width) + r',}[ \t]*', value):
                self.fence = None
        elif not self.math and opening:
            run = opening.group(1)
            self.fence = (run[0], len(run))
        elif value.strip() == '$$':
            self.math = not self.math
        self.probe = ''
        self.candidate = self.fence is None and not self.math

    def feed(self, text):
        output = []
        for char in str(text or ''):
            if len(self.probe) < 256:
                self.probe += char
            if self.candidate:
                self.pending += char
                value = self.pending.lstrip(' \t\r')
                if self.pending.startswith(('    ', '\t')):
                    output.append(self.pending)
                    self.pending = ''
                    self.candidate = False
                elif char == '\n':
                    output.append('\n' if _possible_owned_label(value.rstrip('\r\n')) else self.pending)
                    self.pending = ''
                elif value.endswith(']') and _LABEL.fullmatch(value):
                    self.pending = ''
                    self.probe = ''
                elif len(self.pending) <= 256 and (_MARKER.startswith(value) or _possible_owned_label(value)):
                    continue
                else:
                    output.append(self.pending)
                    self.pending = ''
                    self.candidate = False
            else:
                output.append(char)
            if char == '\n':
                self._newline()
        return ''.join(output)

    def finish(self):
        pending, self.pending = self.pending, ''
        return '' if _possible_owned_label(pending.lstrip(' \t\r')) else pending


def strip_history_labels(text):
    stream = HistoryLabelStream()
    return stream.feed(text) + stream.finish()


async def history_safe_events(original):
    """Project application SSE text frames and always close the source.

    Other events remain unchanged. Cancellation/failure never flushes held text.
    """
    stream = HistoryLabelStream()
    failed = False
    try:
        async for chunk in original:
            if not isinstance(chunk, str):
                yield chunk
                continue
            event, _, rest = chunk.partition('\n')
            if event not in {'event: text', 'event: assistant_status'} or not rest.startswith('data: '):
                yield chunk
                continue
            try:
                data = json.loads(rest[6:].strip())
            except (ValueError, TypeError):
                yield chunk
                continue
            if not isinstance(data, dict):
                yield chunk
                continue
            if event == 'event: assistant_status':
                failed = failed or data.get('phase') == 'failed'
                if failed:
                    stream.finish()
                elif data.get('phase') == 'completed':
                    tail = stream.finish()
                    if tail:
                        yield 'event: text\ndata: ' + json.dumps({'text': tail}, ensure_ascii=False) + '\n\n'
                yield chunk
            elif isinstance(data.get('text'), str):
                visible = stream.feed(data['text'])
                if visible:
                    yield 'event: text\ndata: ' + json.dumps(data | {'text': visible}, ensure_ascii=False) + '\n\n'
            else:
                yield chunk
        tail = stream.finish()
        if tail and not failed:
            yield 'event: text\ndata: ' + json.dumps({'text': tail}, ensure_ascii=False) + '\n\n'
    finally:
        await original.aclose()
