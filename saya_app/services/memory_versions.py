"""Content versions shared by web/QQ memory controls, independent of transport."""
import hashlib
import json
import re


class MemoryVersionConflict(ValueError):
    pass


def record_version(record):
    versioned = {k: v for k, v in record.items() if not k.startswith('_')
        and k not in {'access_count', 'last_accessed'}}
    return hashlib.sha256(json.dumps(versioned, sort_keys=True, ensure_ascii=False,
        separators=(',', ':'), default=str).encode('utf-8')).hexdigest()


def check_version(record, expected):
    if expected is None:
        return
    if not isinstance(expected, str) or not re.fullmatch(r'[0-9a-f]{64}', expected):
        raise ValueError('invalid_memory_version')
    if record is None or record_version(record) != expected:
        raise MemoryVersionConflict('memory_version_changed')


def dependency_version(record):
    # Archiving or moving a source under its summary is storage maintenance,
    # not a change to the facts the summary was generated from.
    keys=('id','title','content','source','project_id','expires_at','sensitivity','do_not_mention')
    return record_version({key:record.get(key) for key in keys})


class CandidateStateSaveError(OSError):
    """Candidate state did not persist; a memory may already be committed."""
    def __init__(self, committed_memory_id=''):
        self.committed_memory_id = committed_memory_id
        super().__init__('candidate_state_not_persisted')
