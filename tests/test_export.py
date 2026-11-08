import json
import unittest
from export.service import ExportError, SnapshotStore, sanitize_snapshot_data


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.store = SnapshotStore(':memory:')

    def tearDown(self):
        self.store.close()

    def test_sanitize_snapshot_strips_internal_logs_and_system_prompts(self):
        raw_data = {
            'title': 'Clean Session',
            'internal_logs': ['debug 1', 'secret token'],
            'other_sessions': ['sess-99'],
            'messages': [
                {'role': 'system', 'content': 'Secret system prompt'},
                {'role': 'user', 'content': 'How do I sort a list?', 'debug_id': 123},
                {'role': 'assistant', 'content': 'Use sorted(items).'},
            ],
        }
        cleaned = sanitize_snapshot_data(raw_data)
        self.assertNotIn('internal_logs', cleaned)
        self.assertNotIn('other_sessions', cleaned)
        self.assertEqual(len(cleaned['messages']), 2)
        self.assertEqual(cleaned['messages'][0], {'role': 'user', 'content': 'How do I sort a list?'})
        self.assertEqual(cleaned['messages'][1], {'role': 'assistant', 'content': 'Use sorted(items).'})

    def test_publish_and_export_conversation_snapshot(self):
        snap = self.store.publish_snapshot(
            title='Python Sorting Q&A',
            snapshot_type='conversation',
            data={
                'messages': [
                    {'role': 'user', 'content': 'Sort list?'},
                    {'role': 'assistant', 'content': 'sorted(x)'},
                ]
            },
            custom_id='snap-conv-1',
        )
        self.assertEqual(snap['snapshot_id'], 'snap-conv-1')
        self.assertEqual(snap['title'], 'Python Sorting Q&A')

        md = self.store.export_as_markdown('snap-conv-1')
        self.assertIn('# Python Sorting Q&A', md)
        self.assertIn('### User', md)
        self.assertIn('Sort list?', md)
        self.assertIn('### Assistant', md)

        js = json.loads(self.store.export_as_json('snap-conv-1'))
        self.assertEqual(js['snapshot_id'], 'snap-conv-1')

        html_doc = self.store.export_as_html('snap-conv-1')
        self.assertIn('<!doctype html>', html_doc)
        self.assertIn('Python Sorting Q&amp;A', html_doc)

        pdf_bytes = self.store.export_as_pdf('snap-conv-1')
        self.assertTrue(pdf_bytes.startswith(b'%PDF-1.4'))
        self.assertTrue(pdf_bytes.strip().endswith(b'%%EOF'))
        self.assertIn(b'Python Sorting Q&A', pdf_bytes)

    def test_publish_and_export_artifact_snapshot(self):
        self.store.publish_snapshot(
            title='Design Spec Snapshot',
            snapshot_type='artifact',
            data={'sections': {'intro': 'System overview', 'api': 'REST endpoints'}},
            custom_id='snap-art-1',
        )
        md = self.store.export_as_markdown('snap-art-1')
        self.assertIn('# Design Spec Snapshot', md)
        self.assertIn('## intro', md)
        self.assertIn('System overview', md)

        html_doc = self.store.export_as_html('snap-art-1')
        self.assertIn('<h2>intro</h2>', html_doc)

        pdf_bytes = self.store.export_as_pdf('snap-art-1')
        self.assertTrue(pdf_bytes.startswith(b'%PDF-1.4'))
        self.assertIn(b'Design Spec Snapshot', pdf_bytes)
        self.assertIn(b'System overview', pdf_bytes)

    def test_invalid_snapshot_inputs_raise_export_error(self):
        with self.assertRaises(ExportError):
            self.store.publish_snapshot('', 'conversation', {})

        with self.assertRaises(ExportError):
            self.store.publish_snapshot('Valid Title', 'unknown_type', {})

        with self.assertRaises(ExportError):
            self.store.export_as_markdown('nonexistent-id')

        with self.assertRaises(ExportError):
            self.store.export_as_pdf('nonexistent-id')


if __name__ == '__main__':
    unittest.main()


