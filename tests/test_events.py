import json
import unittest
from events.contract import EventError, EventStream, to_sse, validate_event


class EventTests(unittest.TestCase):
    def stream(self):
        return EventStream('run-1', clock=lambda: 100.0)

    def test_sequence_increases_per_run(self):
        stream = self.stream()
        first = stream.emit('text_delta', text='Hel')
        second = stream.emit('text_delta', text='lo')
        self.assertEqual((first['sequence'], second['sequence']), (0, 1))
        self.assertEqual(first['run_id'], second['run_id'])

    def test_event_has_contract_fields(self):
        event = self.stream().emit('text_delta', text='Hi')
        self.assertEqual(event, {'run_id': 'run-1', 'sequence': 0, 'type': 'text_delta',
            'payload': {'text': 'Hi'}, 'timestamp': 100.0})

    def test_unknown_type_rejected(self):
        with self.assertRaises(EventError):
            self.stream().emit('shout', text='x')

    def test_no_events_after_done(self):
        stream = self.stream()
        stream.done('completed')
        with self.assertRaises(EventError):
            stream.emit('text_delta', text='late')

    def test_done_needs_final_status(self):
        for status in ('running', 'paused'):
            with self.assertRaises(EventError):
                self.stream().done(status)

    def test_validate_rejects_extra_or_missing_fields(self):
        event = self.stream().emit('metrics', tokens=3)
        with self.assertRaises(EventError):
            validate_event({**event, 'secret': 'x'})
        del event['payload']
        with self.assertRaises(EventError):
            validate_event(event)

    def test_sse_format(self):
        event = self.stream().emit('text_delta', text='Привет')
        message = to_sse(event)
        self.assertTrue(message.startswith('id: 0\nevent: text_delta\ndata: '))
        self.assertTrue(message.endswith('\n\n'))
        self.assertEqual(json.loads(message.split('data: ', 1)[1]), event)


if __name__ == '__main__':
    unittest.main()
