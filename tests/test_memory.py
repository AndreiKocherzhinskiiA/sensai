import asyncio
from contextlib import ExitStack
import importlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from memory.store import MemoryStore, MemoryError


class StoreTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(); self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / 'memory.db'
        self.store = MemoryStore(self.path)

    def create(self):
        return self.store.create('Aurora', 'database', 'PostgreSQL', 'User statement')['created']

    def test_records_survive_reopening_and_crud_preserves_identity(self):
        initial = self.create()
        reopened = MemoryStore(self.path)
        self.assertEqual(reopened.search('aurora')['records'][0], initial)
        updated = reopened.update(initial['id'], 1, 'SQLite', 'User correction')['updated']
        self.assertEqual(updated['id'], initial['id'])
        self.assertEqual(updated['version'], 2)
        self.assertEqual(updated['created_at'], initial['created_at'])
        self.assertEqual(updated['source_note'], 'User correction')
        reopened.delete(initial['id'], 2)
        self.assertEqual(MemoryStore(self.path).search('Aurora')['records'], [])

    def test_duplicate_and_stale_changes_never_overwrite(self):
        item = self.create()
        with self.assertRaisesRegex(MemoryError, 'already exists'):
            self.store.create(' AURORA ', 'Database', 'Wrong', 'duplicate')
        self.store.update(item['id'], 1, 'SQLite', 'correction')
        for operation in (lambda: self.store.update(item['id'], 1, 'Wrong', 'stale'),
                          lambda: self.store.delete(item['id'], 1)):
            with self.assertRaisesRegex(MemoryError, 'changed since'):
                operation()
        self.assertEqual(self.store.search('Aurora')['records'][0]['value'], 'SQLite')

    def test_scopes_and_sql_payload_are_isolated(self):
        item = self.create()
        other = MemoryStore(self.path, scope='another-user')
        self.assertEqual(other.search()['records'], [])
        with self.assertRaises(MemoryError): other.delete(item['id'], 1)
        self.store.create("x'; DROP TABLE memories;--", 'owner', 'Alice', 'user', 'relationship')
        self.assertEqual(self.store.search()['matched_count'], 2)
        self.assertEqual(self.store.search('Alice')['records'][0]['kind'], 'relationship')

    def test_context_limit_never_silently_discards_records(self):
        from memory.pipeline import bounded_records, bounded_history
        for n in range(5):
            self.store.create(str(n), 'description', 'x' * 2000, 'source' * 100)
        result = bounded_records(self.store.search(), max_chars=6000)
        self.assertLess(len(result['records']), 5)
        self.assertTrue(result['truncated'])
        self.assertEqual(result['matched_count'], 5)
        self.assertIn('omitted', result['notice'])
        history = bounded_history([{'role': 'user', 'content': 'x' * 10000}], max_chars=4000)
        self.assertEqual(history['omitted_messages'], 1)

    def test_limits_and_truncation_are_explicit(self):
        for n in range(3): self.store.create(str(n), 'name', 'demo', 'user')
        result = self.store.search('', 2)
        self.assertTrue(result['truncated']); self.assertEqual(result['matched_count'], 3)
        with self.assertRaises(MemoryError): MemoryStore(self.path, max_records=3).create('new', 'name', 'x', 'user')
        with self.assertRaises(MemoryError): self.store.create('', 'name', 'x', 'user')
