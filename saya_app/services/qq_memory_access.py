"""Per-record QQ memory policy; management wired, group consumers still closed.

Caller identity/group authorization must come from verified ingress, never a
model or a display name. ACLs live outside model-generated memory metadata.
"""
from dataclasses import dataclass
import json
import re


class MemoryAccessConflict(Exception):pass
class MemoryAccessDenied(Exception):pass


def valid_id(value):
    return isinstance(value,str) and bool(re.fullmatch(r'[1-9]\d{0,19}',value))


@dataclass(frozen=True)
class MemoryActor:
    bot_id:str
    user_id:str
    verified:bool
    is_owner:bool
    group_id:str=''
    group_authorized:bool=False
    write_enabled:bool=False

    def accepted(self):
        return (self.verified and valid_id(self.bot_id) and valid_id(self.user_id)
                and (not self.group_id or (valid_id(self.group_id) and self.group_authorized)))


@dataclass(frozen=True)
class MemoryAccess:
    bot_id:str
    memory_id:str
    revision:int
    memory_revision:int
    subject_id:str
    visibility:str='private'
    groups:tuple=()
    requesters:tuple=()
    writers:tuple=()


@dataclass(frozen=True)
class MemoryReadGrant:
    bot_id:str
    user_id:str
    group_id:str
    memory_id:str
    memory_revision:int
    policy_revision:int
    sensitivity:str


def can_disclose(actor,policy,memory_id,memory_revision,*,sensitivity='normal'):
    """True only if BOTH requester and complete output audience are allowed.

    Call this before putting content into the model prompt, not just on output.
    Revision mismatch never reuses an old publication grant for edited content.
    """
    if not actor.accepted() or policy is None or policy.memory_revision!=memory_revision:return False
    if policy.bot_id!=actor.bot_id or policy.memory_id!=memory_id:return False
    if sensitivity not in {'normal','personal','sensitive'}:return False
    if not (actor.is_owner or actor.user_id in policy.requesters or '*' in policy.requesters):return False
    if not actor.group_id:
        return True  # An explicit requester grant also authorizes their private reply.
    if policy.visibility=='public':return True
    return policy.visibility=='groups' and actor.group_id in policy.groups


def can_write(actor,policy,memory_id,memory_revision,*,explicit=False,source_speaker_id='',target_group_id=''):
    """Permission to COMMIT; denied proposals must not silently become memories.

    Group members may only write their own explicitly delegated group record;
    they cannot replace the owner's private record by claiming its identity.
    """
    if not actor.accepted() or not actor.write_enabled or not explicit:return False
    if policy is None or policy.memory_revision!=memory_revision:return False
    if policy.bot_id!=actor.bot_id or policy.memory_id!=memory_id:return False
    if source_speaker_id!=actor.user_id:return False
    if actor.is_owner:return True
    return (bool(actor.group_id) and target_group_id==actor.group_id
            and policy.visibility=='groups' and actor.group_id in policy.groups
            and policy.subject_id==actor.user_id and actor.user_id in policy.writers)


class QQMemoryAccessStore:
    """Trusted owner management plane only; content is not stored in this table."""
    def __init__(self,store):
        self.store=store
        with store.connect() as db:
            db.execute('''create table if not exists qq_memory_access(
                bot_id text not null,memory_id text not null,revision integer not null,
                memory_revision integer not null,subject_id text not null,
                visibility text not null,groups_json text not null,
                requesters_json text not null,writers_json text not null,
                primary key(bot_id,memory_id))''')

    def get(self,bot_id,memory_id):
        with self.store.connect() as db:
            row=db.execute('select revision,memory_revision,subject_id,visibility,groups_json,requesters_json,writers_json from qq_memory_access where bot_id=? and memory_id=?',(bot_id,memory_id)).fetchone()
        if not row:return None
        return MemoryAccess(bot_id,memory_id,row[0],row[1],row[2],row[3],tuple(json.loads(row[4])),tuple(json.loads(row[5])),tuple(json.loads(row[6])))

    def authorize_disclosure(self,actor,memory_id,memory_revision,*,sensitivity='normal'):
        policy=self.get(actor.bot_id,memory_id)
        if not can_disclose(actor,policy,memory_id,memory_revision,sensitivity=sensitivity):return None
        return MemoryReadGrant(actor.bot_id,actor.user_id,actor.group_id,memory_id,memory_revision,policy.revision,sensitivity)

    def valid_disclosure(self,actor,grant,current_memory_revision):
        if grant is None or (actor.bot_id,actor.user_id,actor.group_id)!=(grant.bot_id,grant.user_id,grant.group_id):return False
        policy=self.get(actor.bot_id,grant.memory_id)
        return (policy is not None and policy.revision==grant.policy_revision
                and current_memory_revision==grant.memory_revision
                and can_disclose(actor,policy,grant.memory_id,current_memory_revision,sensitivity=grant.sensitivity))

    def authorize_write(self,actor,memory_id,current_memory_revision,**kwargs):
        # Fetch the authoritative policy again at the commit boundary.
        return can_write(actor,self.get(actor.bot_id,memory_id),memory_id,current_memory_revision,**kwargs)

    def set(self,actor,memory_id,memory_revision,subject_id,*,expected_revision=0,
            visibility='private',groups=(),requesters=(),writers=()):
        # Publishing private information is an explicit owner-management action,
        # not something a group message or generated metadata can authorize.
        if not actor.accepted() or not actor.is_owner or actor.group_id:
            raise MemoryAccessDenied('owner_private_management_required')
        if (not isinstance(memory_id,str) or not memory_id or len(memory_id)>128
                or type(memory_revision) is not int or memory_revision<1
                or not valid_id(subject_id) or visibility not in {'private','groups','public'}):
            raise ValueError('invalid_memory_policy')
        groups,requesters,writers=tuple(groups),tuple(requesters),tuple(writers)
        if any(not valid_id(x) for x in groups+writers) or any(x!='*' and not valid_id(x) for x in requesters):raise ValueError('invalid_principal')
        if max(len(groups),len(requesters),len(writers))>128:raise ValueError('policy_limit')
        if visibility=='groups' and not groups:raise ValueError('group_audience_required')
        with self.store.connect() as db:
            db.execute('begin immediate')
            row=db.execute('select revision from qq_memory_access where bot_id=? and memory_id=?',(actor.bot_id,memory_id)).fetchone()
            revision=row[0] if row else 0
            if revision!=expected_revision:raise MemoryAccessConflict('stale_memory_policy')
            db.execute('insert or replace into qq_memory_access values(?,?,?,?,?,?,?,?,?)',
                (actor.bot_id,memory_id,revision+1,memory_revision,subject_id,visibility,
                 json.dumps(sorted(set(groups))),json.dumps(sorted(set(requesters))),json.dumps(sorted(set(writers)))))
        return self.get(actor.bot_id,memory_id)
