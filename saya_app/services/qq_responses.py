"""Trusted display types. Punctuation alone never grants control semantics."""
import time


class ControlReceipt(str):
    content_kind='control_receipt'
    context_eligible=False

    def __new__(cls,text):
        if isinstance(text,cls):return text
        raw=str(text or '').strip()
        value=super().__new__(cls,'【操作回执】“'+raw+'”')
        value.payload=raw
        if hasattr(text,'body'):
            # Compatibility inspection only: dialogue_body still rejects the
            # entire typed receipt, and delivery uses the marked projection.
            value.body=text.body
            value.notes=tuple(getattr(n,'payload',str(n)) for n in getattr(text,'notes',()))
        return value


class ActionProposal(ControlReceipt):
    content_kind='action_proposal'


class ConsoleNotice(ControlReceipt):
    """Brief companion-facing status; still excluded from dialogue/history."""
    def __new__(cls, text):
        value = str.__new__(cls, str(text).strip())
        value.payload = str(value)
        return value


def dialogue_body(answer):
    if isinstance(answer,ControlReceipt):return ''
    return str(getattr(answer,'body',answer) or '')


def is_control_reference(store,message,receipt):
    """Only our persisted receipt identity, scoped to this recipient, is trusted."""
    scope=message.scope+(':shared' if message.shared else ':isolated')
    with store.connect() as db:
        kinds=db.execute("select 1 from sqlite_master where type='table' and name='qq_delivery_part_kinds'").fetchone()
        if kinds:
            row=db.execute('''select 1 from qq_delivery_parts p join qq_deliveries d on d.id=p.result_id
                left join qq_delivery_part_kinds k on k.result_id=p.result_id and k.seq=p.seq
                where d.scope=? and p.receipt=? and p.state='sent' and d.expires>? and
                (d.kind='control' or k.content_kind in ('control_receipt','action_proposal')) limit 1''',(scope,str(receipt),time.time())).fetchone()
            if row:return True
        exists=db.execute("select 1 from sqlite_master where type='table' and name='qq_control_receipt_ids'").fetchone()
        return bool(exists and db.execute('select 1 from qq_control_receipt_ids where receipt=? and scope=? and created>?',
            (str(receipt),scope,time.time()-86400)).fetchone())
