import json
import secrets
import sqlite3
import threading
import time
from typing import Any, Dict, List, Optional, Tuple


class ExportError(Exception):
    """Base error for export and publication."""


def sanitize_snapshot_data(data: Dict[str, Any]) -> Dict[str, Any]:
    sanitized: Dict[str, Any] = {}
    allowed_keys = {'title', 'messages', 'sections', 'version', 'type', 'created_at', 'summary'}
    for k, v in data.items():
        if k in allowed_keys:
            if k == 'messages' and isinstance(v, list):
                sanitized['messages'] = [
                    {'role': m.get('role', 'user'), 'content': m.get('content', '')}
                    for m in v
                    if isinstance(m, dict) and m.get('role') in ('user', 'assistant')
                ]
            else:
                sanitized[k] = v
    return sanitized


def build_pdf_bytes(title: str, blocks: List[Tuple[str, str]]) -> bytes:
    from export.render import render_pdf
    return render_pdf(title, blocks)


class SnapshotStore:
    def __init__(self, db_path: str = ':memory:'):
        self.db_path = db_path
        self._lock = threading.Lock()
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self):
        with self._lock, self.conn:
            self.conn.execute('''
                CREATE TABLE IF NOT EXISTS snapshots (
                    snapshot_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    snapshot_type TEXT NOT NULL,
                    data_json TEXT NOT NULL,
                    created_at REAL NOT NULL
                )
            ''')

    def publish_snapshot(
        self,
        title: str,
        snapshot_type: str,
        data: Dict[str, Any],
        custom_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        title = title.strip()
        if not title:
            raise ExportError('Title cannot be empty.')
        if snapshot_type not in ('conversation', 'artifact'):
            raise ExportError("snapshot_type must be 'conversation' or 'artifact'.")

        snapshot_id = (custom_id or secrets.token_urlsafe(10)).strip()
        cleaned_data = sanitize_snapshot_data(data)
        now = time.time()
        data_json = json.dumps(cleaned_data, ensure_ascii=False)

        try:
            with self._lock, self.conn:
                self.conn.execute('''
                    INSERT INTO snapshots (snapshot_id, title, snapshot_type, data_json, created_at)
                    VALUES (?, ?, ?, ?, ?)
                ''', (snapshot_id, title, snapshot_type, data_json, now))
        except sqlite3.IntegrityError:
            raise ExportError(f'Snapshot {snapshot_id!r} already exists.')

        return self.get_snapshot(snapshot_id)

    def get_snapshot(self, snapshot_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self.conn.execute(
                'SELECT * FROM snapshots WHERE snapshot_id = ?', (snapshot_id,)
            ).fetchone()
            if not row:
                return None
            return {
                'snapshot_id': row['snapshot_id'],
                'title': row['title'],
                'type': row['snapshot_type'],
                'data': json.loads(row['data_json']),
                'created_at': row['created_at'],
            }

    def export_as_markdown(self, snapshot_id: str) -> str:
        snap = self.get_snapshot(snapshot_id)
        if not snap:
            raise ExportError(f'Snapshot {snapshot_id!r} not found.')
        from export.render import render_markdown
        return render_markdown(snap)

    def export_as_json(self, snapshot_id: str) -> str:
        snap = self.get_snapshot(snapshot_id)
        if not snap:
            raise ExportError(f'Snapshot {snapshot_id!r} not found.')
        return json.dumps(snap, indent=2, ensure_ascii=False)

    def export_as_html(self, snapshot_id: str, export_base: Optional[str] = None) -> str:
        snap = self.get_snapshot(snapshot_id)
        if not snap:
            raise ExportError(f'Snapshot {snapshot_id!r} not found.')
        from export.render import render_page
        return render_page(snap, export_base)

    def export_as_pdf(self, snapshot_id: str) -> bytes:
        snap = self.get_snapshot(snapshot_id)
        if not snap:
            raise ExportError(f'Snapshot {snapshot_id!r} not found.')

        from export.render import render_pdf
        data = snap['data']
        blocks: List[Tuple[str, str]] = []
        if snap['type'] == 'conversation':
            for msg in data.get('messages', []):
                blocks.append((msg.get('role', 'user').capitalize(), msg.get('content', '')))
        else:
            for sec_id, content in data.get('sections', {}).items():
                blocks.append((sec_id, content))
        return render_pdf(snap['title'], blocks, snap.get('created_at'), roles=snap['type'] == 'conversation')

    def close(self):
        with self._lock:
            self.conn.close()

