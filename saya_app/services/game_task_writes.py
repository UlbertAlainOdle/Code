"""Durable outcomes for the planner's completed-task recording purpose."""
import time
from .chat_memory_writes import identity,journal
from .memory_origin import bind_origin
from .memory_versions import record_version
from .memory_write_operations import fingerprint


def task_identity(plan_id):
    if not isinstance(plan_id,str) or not plan_id.strip() or len(plan_id)>160:
        raise ValueError('missing_game_plan_identity')
    with bind_origin(None):return identity('game_task',plan_id,plan_id)


def _session(ledger,operation,scope):
    with ledger.store.connect() as db:
        row=db.execute("select session from memory_write_operations where id=? and scope=? and kind='game_task'",
                       (operation,scope)).fetchone()
    return row['session'] if row else None


def result_for(kb,plan_id):
    """Read the original task outcome, without re-running the task or storage."""
    operation,scope=task_identity(plan_id);ledger=journal(kb)
    session=_session(ledger,operation,scope)
    return ledger.lookup(scope,session,operation,kind='game_task') if session else None


def record(kb,plan):
    from .game_memory import _event_recording_enabled,task_event_content
    if not isinstance(plan,dict) or plan.get('status')!='completed' or not _event_recording_enabled():return None
    goal=str(plan.get('goal_description') or '').strip()
    if not goal:return None
    frozen={'plan_id':plan.get('plan_id'),'status':'completed','goal_description':goal,
            'steps':len(plan.get('steps') or [])}
    operation,scope=task_identity(frozen['plan_id'])
    # Keep the original minute-sized content dedupe window. Include the full
    # goal so distinct long goals cannot collide at the old 200-char prefix.
    content=task_event_content({**frozen,'steps':[None]*frozen['steps']})
    event_key=fingerprint([content,goal])
    with kb._memory_write_lock:
        ledger=journal(kb);epoch=kb.memory_use.snapshot()
        session=_session(ledger,operation,scope) or event_key
        def valid(_):
            return (_event_recording_enabled() and plan.get('status')=='completed'
                    and str(plan.get('goal_description') or '').strip()==goal
                    and plan.get('plan_id')==frozen['plan_id']
                    and len(plan.get('steps') or [])==frozen['steps']
                    and kb.memory_use.snapshot()==epoch)
        def write(_):
            with ledger.store.connect() as db:
                other=db.execute("select id from memory_write_operations where scope=? and session=? and kind='game_task' and id!=? "
                    "and json_extract(result,'$[0].state') in ('committed','unknown','pending') order by rowid limit 1",
                    (scope,session,operation)).fetchone()
            if other:return {'state':'not_saved','reason':'duplicate_event','original_operation_id':other['id']}
            with bind_origin(None):
                memory=kb.add_memory(content,title=f'游戏任务：{goal[:40]}',source='game_task',category='event',
                    confidence=.9,tags=['minecraft','game_task'],source_detail={
                        'type':'minecraft_plan_finished','plan_id':frozen['plan_id'],
                        'recorded_at':time.time(),'operation_id':operation})
            return {'state':'committed','memory_id':memory['id'],'version':record_version(memory)}
        return ledger.run(operation,scope,session,'game_task',[frozen],verify=lambda db:True,
                          valid=valid,write=write,retention=30*86400)
