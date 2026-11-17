"""Owner-local previews around the existing personal event settings writer.

Only explicit commands enter this path. The original control audit, card CAS,
and native operation result commit in the same original SQLite transaction.
"""
import asyncio,json
from .qq_event_priority import parse_date
from .qq_tasks import TaskCancelled


def control_spec(console,m):
    if not console.eligible(m) or m.bot_id!=console.b.settings.bot_id:return None
    words=m.text.split()
    if len(words)<2 or words[0]!='/events' or words[1] not in {'keep','unkeep','importance','deadline','schedule'}:return None
    if '\n' in m.text or '\r' in m.text:raise ValueError('请单独输入一项完整事件设置')
    if len(words)==3 and words[1] in {'keep','unkeep'}:return {'action':words[1]}
    if len(words)==4 and words[1]=='importance' and words[3] in {'high','normal','low','auto'}:
        return {'action':'importance','importance':words[3]}
    if len(words)>=4 and words[1] in {'deadline','schedule'}:
        value=' '.join(words[3:])
        if len(value)>80:raise ValueError('日期参数过长，请提供明确日期时间')
        if value!='clear':
            zone=console.b.context_salience.state()['values']['timezone']
            # Unknown QQ timestamps use original host admission time. A later
            # native approval must not reinterpret "tomorrow" on its own day.
            at=m.sent_at
            if not at:
                with console.o.store.connect() as db:row=db.execute('select created from inbox where event_key=?',(m.key,)).fetchone()
                if not row:raise ValueError('缺少原始消息来源')
                at=row['created']
            parsed=parse_date(value,at,zone)
            value=parsed['local'].split('T')[0] if parsed['precision']=='day' else parsed['local']
        return {'action':'timing','timing':{'kind':'deadline' if words[1]=='deadline' else 'appointment','value':value}}
    raise ValueError('请核对事件编号及单项设置参数')


def commit_control(console,m,row,data):
    operations=console.o;service=console.s;controls=service.people.controls;spec=data['native_control']
    if spec['action'] not in {'keep','unkeep','importance','timing'} or spec['action']!=data['plan']['action']:
        raise ValueError('native_control_invalid')
    selected=operations.select(data['plan'],operations.inventory(m,refresh=False))
    if len(selected)!=1 or selected[0]['kind']!='personal':raise ValueError('native_personal_control_required')
    card=selected[0]['card']
    def transaction(db,phase,result):
        if phase=='check':
            console.message(row,data)
            if not service.people.active() or not service.unified.active():raise ValueError('native_control_disabled')
            current=db.execute('select * from qq_event_operation_sessions where id=?',(row['id'],)).fetchone()
            if (not current or current['state']!='native_prepared' or current['data']!=row['data']
                    or current['expires']<=service.clock() or any(current[k]!=row[k] for k in ('bot','actor','scope','request_event'))):
                raise ValueError('native_control_preview_changed')
            fresh=operations.select(data['plan'],operations.inventory(m,refresh=False))
            if operations.snapshot(m,fresh)!=data['snapshot']:raise ValueError('native_control_sources_changed')
            if db.execute('select count(*) from qq_event_operation_results').fetchone()[0]>=8192:raise ValueError('native_control_audit_capacity')
        else:
            audit={'action':spec['action'],'scope':'personal','native_control':True,
                'changes':[{'kind':'personal','id':card['id'],'revision':result['revision'],'changed':result['changed']}]}
            db.execute('insert into qq_event_operation_results values(?,?,?,?,?)',
                (m.key,row['id'],service.clock(),'done',json.dumps(audit)))
            db.execute("update qq_event_operation_sessions set state='done' where id=? and state='native_prepared'",(row['id'],))
            db.execute("update qq_event_operation_sessions set state='superseded' where bot=? and actor=? and scope=? and id<>? and state in ('prepared','waiting','native_prepared')",
                (m.bot_id,m.user_id,operations.scope(m),row['id']))
    try:
        controls._commit(m,card,spec['action'],importance=spec.get('importance'),timing=spec.get('timing'),native_transaction=transaction)
        with operations.store.connect() as db:current=db.execute('select state from qq_event_operation_sessions where id=?',(row['id'],)).fetchone()
        if current['state']=='native_prepared':operations.close(row['id'],'stale')
    except (TaskCancelled,asyncio.CancelledError):raise
    except (ValueError,PermissionError):operations.close(row['id'],'stale')
    except Exception:operations.close(row['id'],'failed')
