"""Durable metadata, bounded evidence and immutable local documents, separate from Graphify jobs."""
from contextlib import contextmanager
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
import sqlite3
from uuid import uuid4
from .models import ResearchError


def now():
    """UTC timestamps describe captured evidence, not a claim that it remains current."""
    return datetime.now(timezone.utc).isoformat()


class ResearchStore:
    """One database owns runs and evidence; LangGraph uses its own checkpoint database."""
    def __init__(self, root: Path):
        self.root = Path(root) / 'research'
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / 'runs.sqlite'
        with self.connection() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript('''CREATE TABLE IF NOT EXISTS runs(
                id TEXT PRIMARY KEY, request_key TEXT UNIQUE, project_id TEXT, body TEXT,
                status TEXT, phase TEXT, created TEXT, updated TEXT, error TEXT, document TEXT);
                CREATE TABLE IF NOT EXISTS evidence(
                run_id TEXT, id TEXT, body TEXT, PRIMARY KEY(run_id,id));''')
            db.execute("UPDATE runs SET status='interrupted', phase='Продолжение после перезапуска требует действия', updated=? WHERE status IN ('queued','running')", (now(),))

    @contextmanager
    def connection(self):
        """Short transactions permit status reads while the dedicated worker captures evidence."""
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def create(self, body: dict) -> tuple[dict, bool]:
        """The same request key/body returns one run; a conflicting retry cannot overwrite it."""
        encoded = json.dumps(body, ensure_ascii=False, sort_keys=True)
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT * FROM runs WHERE request_key=?', (body['requestKey'],)).fetchone()
            if old:
                if old['body'] != encoded:
                    raise ResearchError('Этот ключ уже относится к другому запросу.', 409)
                return self._public(old), False
            if db.execute('SELECT COUNT(*) FROM runs').fetchone()[0] >= 500 or self.path.stat().st_size > 128 * 1024 * 1024:
                raise ResearchError('Локальная история исследований заполнена.', 409)
            identifier, timestamp = uuid4().hex, now()
            db.execute('INSERT INTO runs VALUES(?,?,?,?,?,?,?,?,?,?)',
                       (identifier, body['requestKey'], body['projectId'], encoded, 'queued', 'В очереди', timestamp, timestamp, '', None))
        return self.get(identifier), True

    @staticmethod
    def _public(row) -> dict:
        """Return independent JSON values; SQLite rows never escape their connection."""
        return {'id': row['id'], **json.loads(row['body']), 'status': row['status'], 'phase': row['phase'],
                'createdAt': row['created'], 'updatedAt': row['updated'], 'error': row['error'],
                'document': json.loads(row['document']) if row['document'] else None}

    def existing(self, body: dict):
        """Allow an idempotent retry even when unrelated runs currently fill the queue."""
        encoded = json.dumps(body, ensure_ascii=False, sort_keys=True)
        with self.connection() as db:
            row = db.execute('SELECT * FROM runs WHERE request_key=?', (body['requestKey'],)).fetchone()
        if row and row['body'] != encoded:
            raise ResearchError('Этот ключ уже относится к другому запросу.', 409)
        return self._public(row) if row else None

    def get(self, identifier: str) -> dict:
        """Read one bounded application-owned identifier, never an OS path."""
        if not re.fullmatch(r'[a-f0-9]{32}', identifier):
            raise ResearchError('Исследование не найдено.', 404)
        with self.connection() as db:
            row = db.execute('SELECT * FROM runs WHERE id=?', (identifier,)).fetchone()
        if not row:
            raise ResearchError('Исследование не найдено.', 404)
        return self._public(row)

    def history(self, project_id: str) -> list[dict]:
        """Return only this project's latest 30 run summaries, excluding document bodies."""
        with self.connection() as db:
            # Polling history must not load or decode up to 30 large immutable documents.
            rows = db.execute('SELECT id,request_key,project_id,body,status,phase,created,updated,error,NULL AS document '
                              'FROM runs WHERE project_id=? ORDER BY created DESC LIMIT 30', (project_id,)).fetchall()
        result = []
        for row in rows:
            item = self._public(row)
            item.pop('document')
            result.append(item)
        return result

    def transition(self, identifier: str, status: str, phase: str, *, expected=None, error='', document=None) -> bool:
        """Compare-and-set avoids resume/cancel races; a published document is never replaced."""
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT status,document FROM runs WHERE id=?', (identifier,)).fetchone()
            if not row or row['document'] or (expected and row['status'] not in expected):
                return False
            db.execute('UPDATE runs SET status=?,phase=?,updated=?,error=?,document=? WHERE id=?',
                       (status, phase, now(), error, json.dumps(document, ensure_ascii=False) if document else None, identifier))
        return True

    def capture(self, identifier: str, evidence: dict) -> str:
        """Content-addressed captures make replay idempotent, with an explicit per-run byte/item budget."""
        encoded = json.dumps(evidence, ensure_ascii=False, sort_keys=True)
        eid = 'E' + sha256(encoded.encode()).hexdigest()[:20]
        if len(encoded.encode()) > 48_000:
            raise ResearchError('Выдержка превышает лимит одного источника.')
        with self.connection() as db:
            if db.execute('SELECT 1 FROM evidence WHERE run_id=? AND id=?', (identifier, eid)).fetchone():
                return eid
            count, size = db.execute('SELECT COUNT(*),COALESCE(SUM(length(CAST(body AS BLOB))),0) FROM evidence WHERE run_id=?', (identifier,)).fetchone()
            if count >= 24 or size + len(encoded.encode()) > 512_000:
                raise ResearchError('Достигнут лимит сохранённых источников.')
            db.execute('INSERT INTO evidence VALUES(?,?,?)', (identifier, eid, encoded))
        return eid

    def evidence(self, identifier: str) -> list[dict]:
        """Read captured facts without rereading the project or generating new documentation."""
        with self.connection() as db:
            rows = db.execute('SELECT id,body FROM evidence WHERE run_id=? ORDER BY rowid', (identifier,)).fetchall()
        return [{'id': row['id'], **json.loads(row['body'])} for row in rows]
