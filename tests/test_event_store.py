import unittest
from events.contract import EventStream
from events.store import EventStore, sanitize_payload


class EventStoreTests(unittest.TestCase):
    def setUp(self):
        self.store = EventStore(':memory:')

    def tearDown(self):
        self.store.close()

    def test_record_and_retrieve_events(self):
        event1 = {'run_id': 'run-1', 'sequence': 0, 'type': 'text_delta',
            'payload': {'text': 'Hello'}, 'timestamp': 10.0}
        event2 = {'run_id': 'run-1', 'sequence': 1, 'type': 'metrics',
            'payload': {'tokens': 5, 'latency_ms': 50.0}, 'timestamp': 11.0}
        event3 = {'run_id': 'run-1', 'sequence': 2, 'type': 'done',
            'payload': {'status': 'completed'}, 'timestamp': 12.0}

        self.store.record_event(event1)
        self.store.record_event(event2)
        self.store.record_event(event3)

        events = self.store.get_events('run-1')
        self.assertEqual(len(events), 3)
        self.assertEqual([e['sequence'] for e in events], [0, 1, 2])
        self.assertEqual([e['type'] for e in events], ['text_delta', 'metrics', 'done'])

        run = self.store.get_run('run-1')
        self.assertIsNotNone(run)
        self.assertEqual(run['status'], 'completed')
        self.assertEqual(run['tokens'], 5)
        self.assertEqual(run['latency_ms'], 50.0)

    def test_sanitizes_sensitive_keys(self):
        raw_payload = {
            'text': 'Normal text',
            'api_key': 'secret-12345',
            'nested': {
                'auth_token': 'bearer xyz',
                'ok_field': 'hello',
            },
        }
        sanitized = sanitize_payload(raw_payload)
        self.assertEqual(sanitized['text'], 'Normal text')
        self.assertEqual(sanitized['api_key'], '[REDACTED]')
        self.assertEqual(sanitized['nested']['auth_token'], '[REDACTED]')
        self.assertEqual(sanitized['nested']['ok_field'], 'hello')

    def test_aggregates_metrics_correctly(self):
        stream1 = EventStream('r1', on_event=self.store.record_event)
        stream1.emit('metrics', tokens=10, latency_ms=100.0)
        stream1.done('completed')

        stream2 = EventStream('r2', on_event=self.store.record_event)
        stream2.emit('error', message='Model unavailable')

        stream3 = EventStream('r3', on_event=self.store.record_event)
        stream3.done('cancelled')

        metrics = self.store.get_metrics()
        self.assertEqual(metrics['total_runs'], 3)
        self.assertEqual(metrics['completed_runs'], 1)
        self.assertEqual(metrics['failed_runs'], 1)
        self.assertEqual(metrics['cancelled_runs'], 1)
        self.assertEqual(metrics['total_tokens'], 10)
        self.assertEqual(metrics['avg_latency_ms'], 100.0)
        self.assertAlmostEqual(metrics['error_rate'], 1 / 3, places=2)

    def test_run_metadata_recorded(self):
        self.store.record_run_metadata('meta-run', persona_id='tutor', prompt_version='v1.2')
        stream = EventStream('meta-run', on_event=self.store.record_event)
        stream.done('completed')

        run = self.store.get_run('meta-run')
        self.assertEqual(run['persona_id'], 'tutor')
        self.assertEqual(run['prompt_version'], 'v1.2')
        self.assertEqual(run['status'], 'completed')


if __name__ == '__main__':
    unittest.main()

