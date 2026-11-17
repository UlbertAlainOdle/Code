# Unmodified QQStore class before QQ-03; regression fixture, not application code.
# Source manifest: qq-implementation-manifest-2026-10-28-session12-cancelled.json
# AST SHA256: 0c285dd70569ea82dde170a8e050c5fd87b1dd4a85737795affffb69d424d1ad
class QQStore:
    """Content lives in a separate database, never in the common memory store."""

    def __init__(self, path):
        self.path = Path(path)

    @contextmanager
    def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def initialize(self, retention_days):
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS inbox (
                    id INTEGER PRIMARY KEY, event_key TEXT UNIQUE NOT NULL,
                    scope TEXT NOT NULL, payload TEXT NOT NULL, created REAL NOT NULL,
                    state TEXT NOT NULL, error TEXT NOT NULL DEFAULT '');
                CREATE INDEX IF NOT EXISTS inbox_state ON inbox(state, id);
                CREATE TABLE IF NOT EXISTS conversations (
                    scope TEXT PRIMARY KEY, history TEXT NOT NULL,
                    session_id TEXT NOT NULL, updated REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS group_access (
                    bot_id TEXT NOT NULL, group_id TEXT NOT NULL, enabled INTEGER NOT NULL,
                    updated REAL NOT NULL, PRIMARY KEY(bot_id, group_id));
                CREATE TABLE IF NOT EXISTS group_invites (
                    id TEXT PRIMARY KEY, event_key TEXT UNIQUE NOT NULL,
                    bot_id TEXT NOT NULL, group_id TEXT NOT NULL, user_id TEXT NOT NULL,
                    flag TEXT NOT NULL, created REAL NOT NULL, state TEXT NOT NULL);
            """)
            db.execute("UPDATE inbox SET state='interrupted', error='restart_during_generation', payload='{}' WHERE state='generating'")
            db.execute("UPDATE inbox SET state='delivery_unknown', error='restart_during_send', payload='{}' WHERE state='sending'")
            db.execute("UPDATE group_invites SET state='result_unknown',flag='' WHERE state IN ('approving','rejecting')")
        self.prune(retention_days)

    def prune(self, retention_days):
        cutoff = time.time() - retention_days * 86400
        with self.connect() as db:
            db.execute("DELETE FROM inbox WHERE created < ? AND state != 'queued'", (cutoff,))
            db.execute("DELETE FROM conversations WHERE updated < ?", (cutoff,))
            db.execute("DELETE FROM group_invites WHERE created < ?", (cutoff,))
            db.execute("UPDATE group_invites SET state='expired',flag='' WHERE state='pending' AND created<?", (time.time() - 86400,))

    def queued(self):
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM inbox WHERE state='queued' ORDER BY id")]

    def admit(self, message, settings):
        with self.connect() as db:
            # Serialize check+insert even if an ASGI transport uses multiple threads.
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM inbox WHERE event_key=?", (message.key,)).fetchone():
                return "duplicate", None
            pending = db.execute("SELECT count(*) FROM inbox WHERE state IN ('queued','generating','sending')").fetchone()[0]
            if pending >= settings.queue_size:
                return "busy", None
            recent = db.execute("SELECT 1 FROM inbox WHERE scope=? AND created>? LIMIT 1",
                                (message.scope, time.time() - settings.cooldown_seconds)).fetchone()
            if recent:
                return "rate_limited", None
            cursor = db.execute("INSERT INTO inbox(event_key,scope,payload,created,state) VALUES(?,?,?,?,'queued')",
                                (message.key, message.scope, json.dumps(asdict(message), ensure_ascii=False), time.time()))
            return "queued", cursor.lastrowid

    def set_state(self, row_id, state, error=""):
        with self.connect() as db:
            # Terminal records keep only a dedupe key and content-free diagnostics.
            db.execute("UPDATE inbox SET state=?,error=?,payload=CASE WHEN ? IN ('queued','generating','sending') THEN payload ELSE '{}' END WHERE id=?",
                       (state, error, state, row_id))

    def conversation(self, scope):
        with self.connect() as db:
            row = db.execute("SELECT * FROM conversations WHERE scope=?", (scope,)).fetchone()
            if row:
                return json.loads(row["history"]), row["session_id"]
        return [], "session_qq_" + uuid4().hex + ".jsonl"

    def save_conversation(self, scope, history, session_id):
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO conversations VALUES(?,?,?,?)",
                       (scope, json.dumps(history, ensure_ascii=False), session_id, time.time()))

    def counts(self):
        with self.connect() as db:
            return dict(db.execute("SELECT state,count(*) FROM inbox GROUP BY state").fetchall())

    def group_overrides(self, bot_id):
        with self.connect() as db:
            return {row[0]: bool(row[1]) for row in db.execute(
                "SELECT group_id,enabled FROM group_access WHERE bot_id=?", (bot_id,))}

    def set_group(self, bot_id, group_id, enabled):
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO group_access VALUES(?,?,?,?)",
                       (bot_id, group_id, int(enabled), time.time()))

    def record_invite(self, event, bot_id):
        event_key = hashlib.sha256((bot_id + ":" + event["flag"]).encode()).hexdigest()
        with self.connect() as db:
            existing = db.execute("SELECT id,state FROM group_invites WHERE event_key=?", (event_key,)).fetchone()
            if existing:
                return existing["id"], False
            if db.execute("SELECT count(*) FROM group_invites WHERE bot_id=? AND state='pending'", (bot_id,)).fetchone()[0] >= 128:
                return None, False
            request_id = uuid4().hex[:12]
            db.execute("INSERT INTO group_invites VALUES(?,?,?,?,?,?,?, 'pending')",
                       (request_id, event_key, bot_id, str(event["group_id"]), str(event["user_id"]), event["flag"], time.time()))
            return request_id, True

    def invites(self, bot_id):
        with self.connect() as db:
            return [dict(row) for row in db.execute(
                "SELECT id,group_id,user_id,created,state FROM group_invites WHERE bot_id=? ORDER BY CASE state WHEN 'pending' THEN 0 ELSE 1 END, created DESC LIMIT 128", (bot_id,))]

    def invite(self, bot_id, request_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM group_invites WHERE id=? AND bot_id=?", (request_id, bot_id)).fetchone()
            return dict(row) if row else None

    def claim_invite(self, bot_id, request_id, state):
        with self.connect() as db:
            return db.execute("UPDATE group_invites SET state=? WHERE bot_id=? AND id=? AND state='pending'",
                              (state, bot_id, request_id)).rowcount == 1

    def finish_invite(self, bot_id, request_id, state, enable_group=False):
        with self.connect() as db:
            # Approval state and reply authorization commit together.
            row = db.execute("SELECT group_id FROM group_invites WHERE bot_id=? AND id=?", (bot_id, request_id)).fetchone()
            if enable_group and row:
                db.execute("INSERT OR REPLACE INTO group_access VALUES(?,?,1,?)", (bot_id, row[0], time.time()))
            db.execute("UPDATE group_invites SET state=?,flag='' WHERE bot_id=? AND id=?", (state, bot_id, request_id))
