import unittest
from pathlib import Path

from events.contract import EventStream
from events.store import EventStore
from prompts.versioning import PromptError, PromptVersionStore


class PromptVersioningTests(unittest.TestCase):
    def setUp(self):
        self.prompt_store = PromptVersionStore(':memory:')
        self.event_store = EventStore(':memory:')

    def tearDown(self):
        self.prompt_store.close()
        self.event_store.close()

    def test_initial_version_loaded_from_default(self):
        active = self.prompt_store.get_active_version('system')
        self.assertIsNotNone(active)
        self.assertEqual(active['version_id'], 'v1')
        self.assertIn('Sensai', active['content'])
        self.assertEqual(active['is_active'], 1)

    def test_create_new_version(self):
        v2 = self.prompt_store.create_version(
            name='system',
            content='You are an advanced Sensai tutor with Python focus.',
            author='dev_alice',
            description='Add Python focus',
            activate=True,
        )
        self.assertEqual(v2['version_id'], 'v2')
        self.assertEqual(v2['author'], 'dev_alice')
        self.assertEqual(v2['description'], 'Add Python focus')
        self.assertEqual(v2['is_active'], 1)

        v1 = self.prompt_store.get_version('system', 'v1')
        self.assertEqual(v1['is_active'], 0)

        active = self.prompt_store.get_active_version('system')
        self.assertEqual(active['version_id'], 'v2')

    def test_rollback_version(self):
        self.prompt_store.create_version(
            name='system',
            content='Faulty experimental prompt.',
            author='tester',
            activate=True,
        )
        self.assertEqual(self.prompt_store.get_active_version('system')['version_id'], 'v2')

        restored = self.prompt_store.rollback('system', 'v1')
        self.assertEqual(restored['version_id'], 'v1')
        self.assertEqual(restored['is_active'], 1)

        active = self.prompt_store.get_active_version('system')
        self.assertEqual(active['version_id'], 'v1')

    def test_rollback_unknown_version_raises(self):
        with self.assertRaises(PromptError):
            self.prompt_store.rollback('system', 'v999')

    def test_diff_versions(self):
        self.prompt_store.create_version(
            name='system',
            content='You are Sensai.\nBe concise and fast.',
            activate=False,
        )
        diff = self.prompt_store.diff_versions('system', 'v1', 'v2')
        self.assertEqual(diff['version_a'], 'v1')
        self.assertEqual(diff['version_b'], 'v2')
        self.assertIn('--- system@v1', diff['diff'])
        self.assertIn('+++ system@v2', diff['diff'])
        self.assertGreater(diff['lines_added'], 0)
        self.assertGreater(diff['lines_removed'], 0)

    def test_diff_unknown_version_raises(self):
        with self.assertRaises(PromptError):
            self.prompt_store.diff_versions('system', 'v1', 'v_none')

    def test_compare_performance_with_event_store(self):
        self.prompt_store.create_version(
            name='system',
            content='Sensai version 2 prompt.',
            activate=True,
        )

        s1 = EventStream('r1', on_event=self.event_store.record_event)
        self.event_store.record_run_metadata('r1', prompt_version='v1')
        s1.emit('metrics', tokens=20, latency_ms=100.0)
        s1.done('completed')

        s2 = EventStream('r2', on_event=self.event_store.record_event)
        self.event_store.record_run_metadata('r2', prompt_version='v2')
        s2.emit('metrics', tokens=40, latency_ms=180.0)
        s2.done('completed')

        comp = self.prompt_store.compare_performance(
            self.event_store, 'system', 'v1', 'v2'
        )

        self.assertEqual(comp['version_a']['metrics']['avg_tokens'], 20.0)
        self.assertEqual(comp['version_b']['metrics']['avg_tokens'], 40.0)
        self.assertEqual(comp['comparison']['token_diff'], 20.0)
        self.assertEqual(comp['comparison']['latency_diff_ms'], 80.0)
        self.assertEqual(comp['comparison']['error_rate_diff'], 0.0)

    def test_empty_content_or_name_rejected(self):
        with self.assertRaises(PromptError):
            self.prompt_store.create_version('system', '')
        with self.assertRaises(PromptError):
            self.prompt_store.create_version('   ', 'Valid content')


if __name__ == '__main__':
    unittest.main()

