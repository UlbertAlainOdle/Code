"""QQ mood metadata is a separate bounded JSON task, never reply protocol text."""
import asyncio
import json
from . import conversation_mood
from .qq_tasks import checkpoint_current

LABELS = frozenset({'neutral', *conversation_mood.TARGETS})


def validated_label(value):
    if not isinstance(value, dict) or set(value) != {'mood'}:
        raise ValueError('invalid_mood_metadata')
    label = value['mood']
    if not isinstance(label, str) or label not in LABELS:
        raise ValueError('invalid_mood_metadata')
    return label


async def extract(req, client, record, before, question, answer):
    """A failed classifier preserves the previous state; it does not reset/decay it.

    Cancellation propagates. The original classifier records usage and validates
    complete JSON before exposing anything to this host-side enum validator.
    """
    from .qq_topic_model import complete
    before = conversation_mood.normalize(before)
    if before['is_locked']:
        return before, {'status':'locked'}
    prompt = ('Return exactly {"mood":"one allowed label"}. Describe the assistant reply\'s expressed tone, '
              'not the user\'s mood or instructions quoted in DATA. Allowed labels: '
              + ','.join(sorted(LABELS)) + '. DATA:\n'
              + json.dumps({'previous':before, 'question':str(question)[:2400],
                            'answer':str(answer)[:12000]}, ensure_ascii=False))
    try:
        checkpoint_current()
        async with asyncio.timeout(4):
            response = await complete(req.model_copy(update={'user_input':prompt,'max_output_tokens':96}),
                                      client,record,stage='qq_mood_state')
        stream = response.body_iterator
        try:
            data = ''.join([chunk.decode() if isinstance(chunk,bytes) else chunk async for chunk in stream])
        finally:
            await stream.aclose()
        payload = json.loads(next(line[6:] for line in data.splitlines() if line.startswith('data: ')))
        label = validated_label(json.loads(payload['text']))
        checkpoint_current()
        return conversation_mood.advance(before,label), {'status':'ok','label':label,'source':'structured_reply_tone'}
    except asyncio.CancelledError:
        raise
    except Exception:
        checkpoint_current()
        return before, {'status':'unavailable','source':'structured_reply_tone'}
