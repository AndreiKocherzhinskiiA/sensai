"""Persistent structured facts, separate from chat transcripts and profiles."""
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import re
import sqlite3


class MemoryError(ValueError):
    """A rejected memory operation with no partial write."""


def text_value(value, label, limit, *, empty=False):
    if not isinstance(value, str) or '\x00' in value:
        raise MemoryError(f'{label} must be text without NUL characters.')
    value = value.strip()
    if (not value and not empty) or len(value) > limit:
        raise MemoryError(f'{label} must contain {0 if empty else 1} to {limit} characters.')
    return value


def normalized(value):
    return ' '.join(value.casefold().split())


class MemoryStore:
    """One operator-selected namespace; tool arguments cannot switch users."""

    def __init__(self, path, scope='default', max_records=1000):
        self.path = Path(path)
        self.scope = text_value(scope, 'scope', 120)
        if type(max_records) is not int or not 1 <= max_records <= 100000:
            raise MemoryError('max_records must be between 1 and 100000.')
        self.max_records = max_records
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS memories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    scope TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    subject_key TEXT NOT NULL,
                    predicate TEXT NOT NULL,
                    predicate_key TEXT NOT NULL,
                    value TEXT NOT NULL,
                    kind TEXT NOT NULL CHECK(kind IN ('fact', 'relationship')),
                    source_note TEXT NOT NULL,
                    version INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(scope, subject_key, predicate_key)
                );
                CREATE INDEX IF NOT EXISTS memory_scope ON memories(scope);
            ''')
        self.path.chmod(0o600)

    @contextmanager
    def connection(self, *, write=False):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            if write:
                db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def record(row):
        return {key: row[key] for key in (
            'id', 'subject', 'predicate', 'value', 'kind', 'source_note',
            'version', 'created_at', 'updated_at')}

    @staticmethod
    def timestamp():
        return datetime.now(timezone.utc).isoformat()

    def search(self, query='', limit=10):
        query = text_value(query, 'query', 200, empty=True)
        if type(limit) is not int or not 1 <= limit <= 20:
            raise MemoryError('limit must be between 1 and 20.')
        terms = set(re.findall(r'\w+', normalized(query), re.UNICODE))
        with self.connection() as db:
            rows = db.execute('SELECT * FROM memories WHERE scope = ? ORDER BY updated_at DESC, id DESC',
                              (self.scope,)).fetchall()
        ranked = []
        for row in rows:
            haystack = normalized(' '.join(row[key] for key in ('subject', 'predicate', 'value')))
            score = sum(term in haystack for term in terms)
            if not query or score or normalized(query) in haystack:
                ranked.append((score, self.record(row)))
        ranked.sort(key=lambda item: item[0], reverse=True)
        return {'records': [item[1] for item in ranked[:limit]],
                'matched_count': len(ranked), 'truncated': len(ranked) > limit,
                'query': query, 'scope': self.scope}

    def create(self, subject, predicate, value, source_note, kind='fact'):
        subject = text_value(subject, 'subject', 120)
        predicate = text_value(predicate, 'predicate', 120)
        value = text_value(value, 'value', 2000)
        source_note = text_value(source_note, 'source_note', 1000)
        if kind not in ('fact', 'relationship'):
            raise MemoryError('kind must be fact or relationship.')
        with self.connection(write=True) as db:
            existing = db.execute('SELECT * FROM memories WHERE scope=? AND subject_key=? AND predicate_key=?',
                                  (self.scope, normalized(subject), normalized(predicate))).fetchone()
            if existing:
                raise MemoryError(f'Memory already exists (id={existing["id"]}, version={existing["version"]}). '
                                  'Read it and request an explicit update; nothing was overwritten.')
            count = db.execute('SELECT COUNT(*) FROM memories WHERE scope=?', (self.scope,)).fetchone()[0]
            if count >= self.max_records:
                raise MemoryError('Memory capacity reached; remove an obsolete record before adding another.')
            now = self.timestamp()
            cursor = db.execute('''INSERT INTO memories
                (scope,subject,subject_key,predicate,predicate_key,value,kind,source_note,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?)''',
                (self.scope, subject, normalized(subject), predicate, normalized(predicate), value,
                 kind, source_note, now, now))
            row = db.execute('SELECT * FROM memories WHERE id=?', (cursor.lastrowid,)).fetchone()
        return {'created': self.record(row)}

    def checked_record(self, db, memory_id, expected_version):
        if type(memory_id) is not int or memory_id < 1 or type(expected_version) is not int or expected_version < 1:
            raise MemoryError('memory_id and expected_version must be positive integers from a search result.')
        row = db.execute('SELECT * FROM memories WHERE id=? AND scope=?', (memory_id, self.scope)).fetchone()
        if not row:
            raise MemoryError('Memory does not exist in this scope; nothing changed.')
        if row['version'] != expected_version:
            raise MemoryError('Memory changed since it was read; search again before updating or deleting. Nothing changed.')
        return row

    def update(self, memory_id, expected_version, value, source_note):
        value = text_value(value, 'value', 2000)
        source_note = text_value(source_note, 'source_note', 1000)
        with self.connection(write=True) as db:
            self.checked_record(db, memory_id, expected_version)
            db.execute('UPDATE memories SET value=?, source_note=?, version=version+1, updated_at=? WHERE id=? AND scope=?',
                       (value, source_note, self.timestamp(), memory_id, self.scope))
            row = db.execute('SELECT * FROM memories WHERE id=? AND scope=?', (memory_id, self.scope)).fetchone()
        return {'updated': self.record(row)}

    def delete(self, memory_id, expected_version):
        with self.connection(write=True) as db:
            row = self.checked_record(db, memory_id, expected_version)
            db.execute('DELETE FROM memories WHERE id=? AND scope=?', (memory_id, self.scope))
        return {'deleted': {'id': row['id'], 'subject': row['subject'], 'predicate': row['predicate']},
                'note': 'Removed from structured memory. Existing chats and tool-event history are unchanged.'}
