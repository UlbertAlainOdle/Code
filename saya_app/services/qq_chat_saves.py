"""Compatibility adapter for the original QQ operation table."""
import json
from .memory_write_operations import MemoryWriteOperations, SaveResult, fingerprint


class QQChatSaves(MemoryWriteOperations):
    def __init__(self, store, lock, *, clock=None):
        options = {} if clock is None else {'clock': clock}
        super().__init__(store, lock, table='qq_chat_save_operations', **options)

    def lookup_result(self, scope, operation):
        """Read an exact operation for its actor, independent of its old topic.

        Write/replay lookups retain their original session and plan checks.
        This reader never claims, reconciles, renews or retries an operation.
        """
        with self.store.connect() as db:
            row = db.execute('select result from qq_chat_save_operations '
                             'where id=? and scope=? and expires>?',
                             (operation, scope, self.clock())).fetchone()
        if row is None:
            return None
        return SaveResult(operation, json.loads(row['result']))
