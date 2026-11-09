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


class WebMemoryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        from core.config import settings
        from session import SessionStore
        from events.store import EventStore
        from prompts.versioning import PromptVersionStore
        from structured.results import ResultStore
        directory = tempfile.TemporaryDirectory(); self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        with ExitStack() as initial:
            for name in ('sessions_db', 'events_db', 'prompts_db', 'artifacts_db', 'snapshots_db'):
                initial.enter_context(patch.object(settings, name, root / (name + '.db')))
            initial.enter_context(patch.dict('os.environ', {'SENSAI_MCP_CONFIG': ''}))
            initial.enter_context(patch('structured.api.ResultStore', return_value=ResultStore(root/'results.db')))
            self.web = importlib.import_module('web.app')
        self.store = MemoryStore(root/'memory.db')
        self.sessions = SessionStore(root/'sessions.db')
        self.events = EventStore(':memory:'); self.stack.callback(self.events.close)
        prompts = PromptVersionStore(':memory:'); self.stack.callback(prompts.close)
        for name, value in {'memory_store': self.store, 'runs': {}, 'session_store': self.sessions,
                            'store': self.events, 'prompts': prompts, 'guardrails': None,
                            'mcp_servers': None, 'workspace_writer': None}.items():
            self.stack.enter_context(patch.object(self.web.app.state, name, value, create=(name == 'memory_store')))
        self.stack.enter_context(patch.dict('os.environ', {'SENSAI_MEMORY_ENABLED': 'true'}))
        self.stack.enter_context(patch.object(settings, 'check_ollama_alive', return_value=True))
        self.stack.enter_context(patch('core.chat.retrieve_advanced', return_value=[]))
        self.model = self.stack.enter_context(patch('memory.pipeline.generate_json', new_callable=AsyncMock))
        self.answer = self.stack.enter_context(patch.object(self.web, 'execute_turn_stream', side_effect=lambda **kw: iter(['Observed memory answer.'])))

    async def request(self, method, path, data=None, events=None):
        output = []; delivered = False
        async def receive():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {'type': 'http.request', 'body': json.dumps(data).encode() if data else b'', 'more_body': False}
            await asyncio.Event().wait()
        async def send(message):
            output.append(message)
            if events is not None and message['type'] == 'http.response.body':
                for line in message.get('body', b'').decode().splitlines():
                    if line.startswith('data: '): events.put_nowait(json.loads(line[6:]))
        scope = {'type': 'http', 'asgi': {'version': '3.0', 'spec_version': '2.4'},
                 'http_version': '1.1', 'method': method, 'scheme': 'http', 'path': path,
                 'raw_path': path.encode(), 'query_string': b'', 'root_path': '',
                 'headers': [(b'content-type', b'application/json')],
                 'server': ('test', 80), 'client': ('127.0.0.1', 1234)}
        await self.web.app(scope, receive, send)
        return next(m['status'] for m in output if m['type'] == 'http.response.start'), b''.join(m.get('body', b'') for m in output).decode()

    async def start(self, message='Remember that Aurora uses PostgreSQL.', **extra):
        status, body = await self.request('POST', '/api/runs', {'message': message,
            'mode': 'ollama', 'tools_enabled': False, 'memory_enabled': True, **extra})
        self.assertEqual(status, 200, body)
        run = json.loads(body); queue = asyncio.Queue()
        task = asyncio.create_task(self.request('GET', '/api/runs/'+run['run_id']+'/events', events=queue))
        async def cleanup():
            if not task.done():
                task.cancel()
                try: await task
                except asyncio.CancelledError: pass
        self.addAsyncCleanup(cleanup)
        return run, task, queue

    async def approval(self, queue):
        while True:
            event = await asyncio.wait_for(queue.get(), 5)
            if event['type'] == 'approval_request': return event['payload']

    async def decide(self, run, approval, approved=True):
        return await self.request('POST', '/api/runs/'+run['run_id']+'/memory/decision',
                                  {'approval_id': approval['approval_id'], 'approved': approved})

    def creation(self):
        self.model.side_effect = [{'action': 'create', 'query': 'Aurora'},
            {'subject': 'Aurora', 'predicate': 'database', 'value': 'PostgreSQL', 'kind': 'fact'}]

    async def test_crud_across_new_chats_without_mcp(self):
        self.creation()
        first, task, queue = await self.start()
        approval = await self.approval(queue)
        self.assertEqual(self.store.search()['records'], [])
        self.assertEqual((await self.decide(first, approval))[0], 200)
        await asyncio.wait_for(task, 5)
        item = self.store.search('Aurora')['records'][0]
        self.assertIn(first['session_id'], item['source_note'])
        self.model.side_effect = [{'action': 'read', 'query': 'Aurora'}]
        second, task, _ = await self.start('Which database does Aurora use?')
        await asyncio.wait_for(task, 5)
        self.assertNotEqual(first['session_id'], second['session_id'])
        self.assertIn('PostgreSQL', str(self.answer.call_args.kwargs['to_send']))
        self.assertFalse(self.answer.call_args.kwargs['cache_context_independent'])
        self.model.side_effect = [{'action': 'update', 'query': 'Aurora'}, {'memory_id': item['id'], 'value': 'SQLite'}]
        third, task, queue = await self.start('Update the remembered Aurora database to SQLite.')
        approval = await self.approval(queue)
        await self.decide(third, approval); await asyncio.wait_for(task, 5)
        self.assertEqual(self.store.search('Aurora')['records'][0]['value'], 'SQLite')
        self.model.side_effect = [{'action': 'delete', 'query': 'Aurora'}, {'memory_id': item['id']}]
        fourth, task, queue = await self.start('Forget the saved database for Aurora.')
        await self.decide(fourth, await self.approval(queue)); await asyncio.wait_for(task, 5)
        self.assertEqual(self.store.search('Aurora')['records'], [])

    async def test_reject_and_replay_cannot_write(self):
        self.creation(); run, task, queue = await self.start()
        approval = await self.approval(queue)
        await self.decide(run, approval, False); await asyncio.wait_for(task, 5)
        self.assertEqual(self.store.search()['records'], [])
        self.assertEqual((await self.decide(run, approval))[0], 409)
        self.answer.assert_not_called()

    async def test_disabled_and_temporary_chat_do_not_touch_memory(self):
        for options in ({'memory_enabled': False}, {'temporary': True}):
            with self.subTest(options=options):
                run, task, _ = await self.start(**options)
                await asyncio.wait_for(task, 5)
                self.model.assert_not_called()
                self.assertNotIn('<memory_observation>', str(self.answer.call_args.kwargs['to_send']))
        self.assertEqual(self.store.search()['records'], [])

    async def test_clear_cancels_pending_approval_without_write(self):
        self.creation(); run, task, queue = await self.start()
        approval = await self.approval(queue)
        status, _ = await self.request('POST', '/api/sessions/'+run['session_id']+'/clear')
        self.assertEqual(status, 200)
        await asyncio.wait_for(task, 5)
        self.assertEqual(self.store.search()['records'], [])
        self.assertEqual((await self.decide(run, approval))[0], 409)

    async def test_concurrent_update_invalidates_approved_version(self):
        item = self.store.create('Aurora', 'database', 'PostgreSQL', 'user')['created']
        self.model.side_effect = [{'action': 'update', 'query': 'Aurora'}, {'memory_id': item['id'], 'value': 'SQLite'}]
        run, task, queue = await self.start('Change Aurora database to SQLite.')
        approval = await self.approval(queue)
        self.store.update(item['id'], 1, 'MariaDB', 'newer change')
        await self.decide(run, approval); await asyncio.wait_for(task, 5)
        self.assertEqual(self.store.search('Aurora')['records'][0]['value'], 'MariaDB')
        self.answer.assert_not_called()
