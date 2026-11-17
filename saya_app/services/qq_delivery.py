"""Bounded, recipient-scoped logical replies and crash-safe segment receipts.

Only the active QQ consumer sends. Persist intent before transport; an uncertain
segment is never replayed. Reading the remainder never runs the responder.
"""

import json
import re
import time
import unicodedata
from uuid import uuid4
from .qq_presentation import clean_control_output, project_operation_hints
from .qq_tasks import effect_current, current_guard
from .qq_responses import ControlReceipt, dialogue_body


TTL = 86400
MAX_RESULTS = 128
MAX_SCOPE_RESULTS = 8
MAX_CHARS = 48000
HARD_PART_CHARS = 2880  # Room for reference, counters and continuation hint.


class QQAnswer(str):
    """String-compatible projection carrying authoritative receipts separately."""

    def __new__(cls, body, notes, *, mood_state=None,context_eligible=True):
        notes = tuple(dict.fromkeys(note if isinstance(note,str) else str(note) for note in notes if note))
        value = super().__new__(cls, body + ('\n\n' + '\n'.join(notes) if notes else ''))
        value.body, value.notes = body, notes
        value.mood_state = mood_state
        value.context_eligible=context_eligible
        return value


def clean(text):
    return clean_control_output(text)


def _safe_end(text, end):
    """Keep combining sequences, emoji joins and regional flag pairs together."""
    while 0 < end < len(text):
        current, previous = text[end], text[end - 1]
        if (unicodedata.combining(current) or current in '\ufe0e\ufe0f\u200d'
                or previous == '\u200d' or '\U0001f3fb' <= current <= '\U0001f3ff'):
            end -= 1
            continue
        if '\U0001f1e6' <= current <= '\U0001f1ff' and '\U0001f1e6' <= previous <= '\U0001f1ff':
            count = 0
            for char in reversed(text[:end]):
                if not '\U0001f1e6' <= char <= '\U0001f1ff':
                    break
                count += 1
            if count % 2:
                end -= 1
                continue
        break
    if end == 0:
        # A single pathological grapheme can exceed the transport budget.
        raise ValueError('unbreakable_grapheme')
    return end


def semantic_parts(text, target):
    """Sentence/paragraph boundaries first; code fences reopen per bubble.

    URLs and inline code are atomic when within the hard protocol budget.
    Markdown tables remain readable plain text, without any HTML rendering.
    """
    text = clean(text)
    parts = []
    code = ''
    while text:
        prefix = code if code else ''
        # Closing the fence costs at most four characters.
        budget = HARD_PART_CHARS - len(prefix) - 4
        end = min(len(text), target if not code else budget)
        if end < len(text):
            # Never cut a link/inline-code token. Overlong URLs remain safely
            # cached but cannot fit a QQ bubble; fail rather than emit a bad URL.
            tokens = list(re.finditer(r'https?://[^\s<>]+|`[^`\n]+`', text))
            for token in tokens:
                if token.start() < end < token.end():
                    if token.end() - token.start() > budget:
                        raise ValueError('unbreakable_token')
                    end = token.start() if token.start() else token.end()
                    break
            if not code:
                # Keep a small fenced block intact, even beyond the soft target.
                opening = text.find('```', 0, end)
                if opening >= 0:
                    closing = text.find('```', opening + 3)
                    if closing >= 0 and closing + 3 <= budget:
                        end = closing + 3
                    elif opening > 0:
                        end = opening
                    else:
                        end = min(len(text), budget)
            if end > budget:
                end = budget
            # Prefer a paragraph, line or sentence ending in the latter half.
            matches = [match for match in re.finditer(r'\n|[。！？!?；;](?:[”’」』）)]|\s)*|\.\s+', text[:end])
                       if not any(token.start() < match.end() < token.end() for token in tokens)]
            if matches and matches[-1].end() >= end // 2:
                end = matches[-1].end()
            end = _safe_end(text, end)
        piece, text = text[:end], text[end:]
        # Fences in input are plain text. Reopen long code on the next part.
        fences = list(re.finditer(r'(?m)^```([^\n`]*)[^\n]*\n?|```', piece))
        for fence in fences:
            code = '' if code else '```' + (fence.group(1) or '')[:40] + '\n'
        shown = prefix + piece
        if code:
            shown = shown.rstrip('\n') + '\n```'
        if shown.strip():
            parts.append(shown.strip())
    return parts


def natural_parts(text, target, limit=3):
    """Paragraph-first chat bubbles; structured content stays conservative."""
    text = clean(text)
    paragraphs = [value.strip() for value in re.split(r'\n\s*\n|\n', text) if value.strip()]

    def fragment(value):
        compact = re.sub(r'\s+', '', value)
        # Short is not synonymous with incomplete: a complete 10-29 character
        # paragraph can be a useful standalone reply. Merge only tiny asides or
        # short unfinished fragments, not every paragraph below30 characters.
        return len(compact) < 9 or (len(compact) < 20 and not re.search(r'[。！？.!?…][”’"\')）」』]*$', compact))

    if len(text) <= 60 and sum(not fragment(p) for p in paragraphs) < 2:
        return [text] if text else []
    # Do not mistake list rows, quoted speech or fenced code for chat bubbles.
    if re.search(r'(?m)^\s*(?:```|~~~|>|[-*+]\s|\d+[.)、]\s)|\|.*\||\$\$', text):
        return semantic_parts(text, HARD_PART_CHARS - 4)
    parts = []
    for paragraph in paragraphs:
        soft = min(target, 120) if re.search(r'[。！？!?；;]|\.\s', paragraph) else target
        for piece in semantic_parts(paragraph, soft):
            if parts and (fragment(parts[-1]) or fragment(piece)) and len(parts[-1]) + len(piece) + 2 <= target:
                parts[-1] += '\n\n' + piece
            else:
                parts.append(piece)
    # Preserve complete ideas even when they exceed the current send batch.
    # The delivery batch/continuation controls handle quantity, not paragraph
    # merging. Tiny fragments above still attach to their adjacent sentence.
    return parts


def initialize(store):
    with store.connect() as db:
        db.executescript('''
            CREATE TABLE IF NOT EXISTS qq_deliveries (
                id TEXT PRIMARY KEY, event_key TEXT UNIQUE NOT NULL,
                scope TEXT NOT NULL, origin TEXT NOT NULL, kind TEXT NOT NULL,
                canonical TEXT NOT NULL, created REAL NOT NULL, expires REAL NOT NULL);
            CREATE INDEX IF NOT EXISTS qq_delivery_scope ON qq_deliveries(scope,created);
            CREATE TABLE IF NOT EXISTS qq_delivery_parts (
                result_id TEXT NOT NULL, seq INTEGER NOT NULL, text TEXT NOT NULL,
                state TEXT NOT NULL, receipt TEXT NOT NULL DEFAULT '',
                PRIMARY KEY(result_id,seq));
            CREATE TABLE IF NOT EXISTS qq_delivery_options (
                result_id TEXT PRIMARY KEY, strategy TEXT NOT NULL,
                subject TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS qq_delivery_stops (
                result_id TEXT PRIMARY KEY, reason TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS qq_delivery_memory_epochs (
                result_id TEXT PRIMARY KEY, epoch TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS qq_delivery_part_kinds (
                result_id TEXT NOT NULL,seq INTEGER NOT NULL,content_kind TEXT NOT NULL,
                PRIMARY KEY(result_id,seq));
            CREATE TABLE IF NOT EXISTS qq_control_receipt_ids (
                receipt TEXT NOT NULL,scope TEXT NOT NULL,created REAL NOT NULL,
                PRIMARY KEY(receipt,scope));
        ''')
        db.execute("UPDATE qq_delivery_parts SET state='unknown' WHERE state='sending'")
    prune(store)


def prune(store, now=None):
    now = time.time() if now is None else now
    with store.connect() as db:
        db.execute('DELETE FROM qq_deliveries WHERE expires<=?', (now,))
        db.execute('DELETE FROM qq_deliveries WHERE id IN (SELECT id FROM qq_deliveries ORDER BY created DESC,rowid DESC LIMIT -1 OFFSET ?)', (MAX_RESULTS,))
        db.execute('DELETE FROM qq_delivery_parts WHERE result_id NOT IN (SELECT id FROM qq_deliveries)')
        db.execute('DELETE FROM qq_delivery_stops WHERE result_id NOT IN (SELECT id FROM qq_deliveries)')
        db.execute('DELETE FROM qq_delivery_options WHERE result_id NOT IN (SELECT id FROM qq_deliveries)')
        db.execute('DELETE FROM qq_delivery_memory_epochs WHERE result_id NOT IN (SELECT id FROM qq_deliveries)')
        db.execute('DELETE FROM qq_delivery_part_kinds WHERE result_id NOT IN (SELECT id FROM qq_deliveries)')
        db.execute('DELETE FROM qq_control_receipt_ids WHERE created<=?',(now-TTL,))
        for table in ['qq_forward_results','qq_forward_sources','qq_expression_deliveries','qq_forward_input_results','qq_episode_result_sources']:
            if db.execute("select 1 from sqlite_master where type='table' and name=?",(table,)).fetchone():
                db.execute('delete from '+table+' where result_id not in (select id from qq_deliveries)')


def create(store, message, scope, answer, target, kind='final', *, strategy='length', limit=3, memory_epoch=None):
    prune(store)
    if kind=='control':answer=ControlReceipt(answer)
    canonical = clean(dialogue_body(answer) if kind=='final' else str(answer))
    if not canonical:
        raise ValueError('empty_reply')
    notes = getattr(answer, 'notes', ()) if kind=='final' else ()
    body = clean(getattr(answer, 'body', canonical) if kind=='final' else canonical)
    # Receipts go first so the initial batch cannot hide a committed write.
    # Keep authoritative notes in distinct transport parts. Only character
    # dialogue may become future history or merged-forward character speech.
    split=lambda text:natural_parts(text,target,limit) if strategy=='natural' and kind=='final' else semantic_parts(text,target)
    groups=[(str(note),getattr(note,'content_kind','evidence')) for note in notes]
    groups.append((body,'dialogue' if kind=='final' else getattr(answer,'content_kind','control_receipt')))
    typed_parts=[];remaining=MAX_CHARS
    for text,content_kind in groups:
        if remaining<40:break
        if len(text)>remaining:
            text=text[:_safe_end(text,remaining-35)]+'\n（超过本机结果容量，后续内容未保留。）'
        remaining-=len(text)
        pieces=split(text) if content_kind=='dialogue' else semantic_parts(text,target)
        typed_parts.extend((piece,content_kind) for piece in pieces)
    parts=[p for p,_ in typed_parts]
    if not parts:
        raise ValueError('empty_reply')
    result_id, now = uuid4().hex[:16], time.time()
    # Persist only the authenticated transport identity, never the prompt.
    origin = {key:getattr(message,key) for key in ('bot_id','user_id','group_id','message_id')}
    with store.connect() as db:
        db.execute('INSERT INTO qq_deliveries VALUES(?,?,?,?,?,?,?,?)',
                   (result_id, message.key, scope, json.dumps(origin), kind, canonical[:MAX_CHARS], now, now+TTL))
        db.executemany("INSERT INTO qq_delivery_parts VALUES(?,?,?,'pending','')",
                       [(result_id,index,text) for index,text in enumerate(parts)])
        db.executemany('INSERT INTO qq_delivery_part_kinds VALUES(?,?,?)',
                       [(result_id,index,kind) for index,(_,kind) in enumerate(typed_parts)])
        db.execute('INSERT INTO qq_delivery_options VALUES(?,?,?)',
                   (result_id, strategy, clean(message.text).replace('\n',' ')[:60]))
        if memory_epoch is not None:
            db.execute('insert into qq_delivery_memory_epochs values(?,?)', (result_id, memory_epoch))
        stale = db.execute('SELECT id FROM qq_deliveries WHERE scope=? ORDER BY created DESC,rowid DESC LIMIT -1 OFFSET ?', (scope,MAX_SCOPE_RESULTS)).fetchall()
        for row in stale:
            db.execute('DELETE FROM qq_deliveries WHERE id=?', (row[0],))
        db.execute('DELETE FROM qq_deliveries WHERE id IN (SELECT id FROM qq_deliveries ORDER BY created DESC,rowid DESC LIMIT -1 OFFSET ?)', (MAX_RESULTS,))
        db.execute('DELETE FROM qq_delivery_parts WHERE result_id NOT IN (SELECT id FROM qq_deliveries)')
        db.execute('DELETE FROM qq_delivery_options WHERE result_id NOT IN (SELECT id FROM qq_deliveries)')
    return result_id


def lookup(store, scope, result_id=None):
    prune(store)
    with store.connect() as db:
        if result_id:
            row = db.execute('SELECT * FROM qq_deliveries WHERE id=? AND scope=?', (result_id,scope)).fetchone()
        else:
            row = db.execute("SELECT * FROM qq_deliveries WHERE scope=? AND kind='final' AND EXISTS(SELECT 1 FROM qq_delivery_parts p WHERE p.result_id=qq_deliveries.id AND p.state!='sent') ORDER BY created DESC,rowid DESC LIMIT 1", (scope,)).fetchone()
        if not row:
            return None, []
        return dict(row), [dict(p) for p in db.execute('''SELECT p.*,coalesce(k.content_kind,
            case when ?='control' then 'control_receipt' else 'dialogue' end) content_kind
            FROM qq_delivery_parts p LEFT JOIN qq_delivery_part_kinds k ON k.result_id=p.result_id AND k.seq=p.seq
            WHERE p.result_id=? ORDER BY p.seq''',(row['kind'],row['id']))]


def sent_dialogue(store,scope,result_id):
    record,parts=lookup(store,scope,result_id)
    return ''.join(p['text'] for p in parts if p['state']=='sent' and p['content_kind']=='dialogue') if record else ''


def clear_scope(store, scope):
    with store.connect() as db:
        db.execute('DELETE FROM qq_delivery_parts WHERE result_id IN (SELECT id FROM qq_deliveries WHERE scope=?)', (scope,))
        db.execute('DELETE FROM qq_deliveries WHERE scope=?', (scope,))
        db.execute('DELETE FROM qq_delivery_options WHERE result_id NOT IN (SELECT id FROM qq_deliveries)')
    prune(store)


def topics(store, scope):
    """Only explicitly requested, same-recipient unfinished results; no LLM."""
    prune(store)
    with store.connect() as db:
        rows = db.execute("""SELECT d.id,o.subject,d.expires,
            SUM(CASE WHEN p.state='pending' THEN 1 ELSE 0 END) AS remaining,
            SUM(CASE WHEN p.state IN ('sending','unknown') THEN 1 ELSE 0 END) AS uncertain
            FROM qq_deliveries d JOIN qq_delivery_parts p ON p.result_id=d.id
            LEFT JOIN qq_delivery_options o ON o.result_id=d.id
            WHERE d.scope=? AND d.kind='final' AND NOT EXISTS (SELECT 1 FROM qq_delivery_stops s WHERE s.result_id=d.id) GROUP BY d.id HAVING remaining>0 OR uncertain>0
            ORDER BY d.created DESC LIMIT 8""", (scope,)).fetchall()
    if not rows:
        return '目前没有没说完的话题。'
    lines = ['这些话题还没说完（保存时间最长24小时）：']
    for row in rows:
        title = row['subject'] or '较早的话题'
        detail = '发送状态不明，暂不能续读' if row['uncertain'] else f"还有{row['remaining']}条；/continue {row['id']}"
        lines.append(f"{title}\n{detail}")
    return '\n\n'.join(lines)


async def send_live_control(store, client, message, scope, answer, reference):
    """Deliver source-bound receipts without retaining a resumable text copy."""
    # A plain host receipt has no model evidence to resume or re-authorize.
    # Keep the richer maintenance receipt's source validation unchanged.
    plain=type(answer) is ControlReceipt
    parts=[str(answer)] if plain else answer.parts
    async def send(text, first):
        with effect_current('send', scope=scope) as effect:
            if callable(getattr(client, 'send_part', None)):
                receipt = await client.send_part(message, text, reference=bool(reference and first), mention=first)
            else:
                receipt = await client.send(message, text)
            if not re.fullmatch(r'-?\d{1,20}', str(receipt or '')):
                raise RuntimeError('missing_control_delivery_receipt')
            effect.committed()
            with store.connect() as db:
                db.execute('insert or ignore into qq_control_receipt_ids values(?,?,?)',
                           (str(receipt), scope, time.time()))
                db.execute('delete from qq_control_receipt_ids where rowid in '
                           '(select rowid from qq_control_receipt_ids order by created desc limit -1 offset 2048)')
                from .qq_event_operations import QQEventOperations
                QQEventOperations.record_outbound(db, scope)

    for index, part in enumerate(parts):
        if not plain and not answer.valid():
            await send(str(ControlReceipt('来源或权限已变化，请重新读取联合视图。')), index == 0)
            return
        await send(part, index == 0)
    if not plain and answer.delivered:
        answer.delivered()


async def send_batch(store, client, message, scope, result_id, limit, reference, *, memory_check=None, expressions=None):
    if memory_check is not None and not memory_check():
        return '记忆已修改，旧回答不再续读。请发送新问题。'
    guard = current_guard()
    if guard is not None:
        if guard.scope != scope:
            raise KeyError('delivery scope does not match task')
        guard.result_ids.add(result_id)
        guard.registry.attach_result(guard.task_id,scope,result_id)
        guard.checkpoint()
    record, parts = lookup(store, scope, result_id)
    if not record:
        return '结果不存在、已过期，或不属于当前会话。'
    with store.connect() as db:
        stopped = db.execute('SELECT reason FROM qq_delivery_stops WHERE result_id=?', (result_id,)).fetchone()
    if stopped:
        if stopped['reason'] == 'memory_changed':
            return '记忆已修改，旧回答不再续读。请发送新问题。'
        return '该任务已取消，未发内容不会继续发送。' + ('其中存在发送不明的分段，不能保证撤回。' if any(p['state']=='unknown' for p in parts) else '')
    if any(p['state'] in {'sending','unknown'} for p in parts):
        return '这份回答有一段发送结果不明，为避免重复，不会自动重发或跳过。请发送新问题。'
    pending = [p for p in parts if p['state']=='pending']
    if not pending:
        return '这份回答已经读完。'
    origin = json.loads(record['origin'])
    # The stored identity was admitted by the same consumer; caller scope is
    # checked first. Use original question reference, including on continuation.
    from dataclasses import replace
    target = replace(message, **origin)
    batch = pending[:limit]
    with store.connect() as db:
        options = db.execute('SELECT strategy FROM qq_delivery_options WHERE result_id=?',(result_id,)).fetchone()
    natural = options is not None and options['strategy'] == 'natural'
    for index, part in enumerate(batch):
        if memory_check is not None and not memory_check():
            return '记忆已修改，后续旧内容已停止。请发送新问题。'
        number = f"[{part['seq']+1}/{len(parts)}] " if len(parts)>1 and not natural else ''
        # Old pending results can predate this filter. Preserve receipt/counter
        # identity and project only at send time; never replay sent segments.
        text = number + (project_operation_hints(clean(part['text']), message.text) or '（这段没有可显示的正文。）')
        # The reservation atomically rejects a cancellation before touching
        # this segment. In-flight transport still needs its real receipt.
        with effect_current('send', scope=scope) as effect:
            with store.connect() as db:
                claimed = db.execute("UPDATE qq_delivery_parts SET state='sending' WHERE result_id=? AND seq=? AND state='pending'", (result_id,part['seq'])).rowcount
            if claimed != 1:
                effect.failed()
                raise RuntimeError('delivery_claim_conflict')
            decoration=None
            try:
                decoration=expressions.inline_part(target,scope,result_id,text) if expressions and callable(getattr(client,'send_inline',None)) else None
                if decoration and callable(getattr(client,'send_inline',None)):
                    receipt=await client.send_inline(target,text,**decoration,reference=bool(reference and index==0),mention=index==0)
                    expressions.inline_result(result_id,'inline_sent',receipt)
                elif callable(getattr(client,'send_part',None)):
                    receipt = await client.send_part(target,text,reference=bool(reference and index==0),mention=index==0)
                else:
                    # Backward-compatible injected transports used by older callers.
                    receipt = await client.send(target,text)
                effect.committed()
                with store.connect() as db:
                    db.execute("UPDATE qq_delivery_parts SET state='sent',receipt=? WHERE result_id=? AND seq=?", (str(receipt or ''),result_id,part['seq']))
                    from .qq_event_operations import QQEventOperations
                    QQEventOperations.record_outbound(db,scope)
                if hasattr(message,'experience_started'):
                    timings=getattr(message,'experience_timing',{})
                    elapsed=(time.monotonic()-message.experience_started)*1000
                    timings.setdefault('first_bubble_ms',elapsed)
                    timings['text_complete_ms']=elapsed
                    message.experience_timing=timings
            except BaseException:
                if expressions and decoration:expressions.inline_result(result_id,'unknown','')
                with store.connect() as db:
                    db.execute("UPDATE qq_delivery_parts SET state='unknown' WHERE result_id=? AND seq=?", (result_id,part['seq']))
                raise
    return None


def freeze(store, scope, result_id):
    with store.connect() as db:
        if not db.execute('SELECT 1 FROM qq_deliveries WHERE id=? AND scope=?', (result_id,scope)).fetchone():
            return
        db.execute("INSERT OR IGNORE INTO qq_delivery_stops VALUES(?,'cancelled')", (result_id,))
        db.execute("UPDATE qq_delivery_parts SET state='cancelled' WHERE result_id=? AND state='pending'", (result_id,))
