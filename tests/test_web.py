import json
import os
import socket
import tempfile
import threading
import time
import unittest

import requests
import uvicorn

from artifacts.service import ArtifactStore
from events.store import EventStore
from export.service import SnapshotStore
from prompts.versioning import PromptVersionStore
from session.store import SessionStore
from web.app import app


def parse_sse(text):
    events = []
    for block in text.strip().split('\n\n'):
        data = [line[len('data: '):] for line in block.split('\n') if line.startswith('data: ')]
        events.append(json.loads(data[0]))
    return events


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


class WebStubTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        port = free_port()
        cls.url = f'http://127.0.0.1:{port}'
        cls.server = uvicorn.Server(uvicorn.Config(app, port=port, log_level='warning'))
        cls.thread = threading.Thread(target=cls.server.run, daemon=True)
        cls.thread.start()
        deadline = time.time() + 5
        while not cls.server.started and time.time() < deadline:
            time.sleep(0.01)

    @classmethod
    def tearDownClass(cls):
        cls.server.should_exit = True
        cls.thread.join()

    def setUp(self):
        app.state.delay = 0
        app.state.runs.clear()
        self.tmp_session_file = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self.tmp_session_file.close()
        app.state.session_store = SessionStore(self.tmp_session_file.name)
        app.state.store = EventStore(':memory:')
        app.state.prompts = PromptVersionStore(':memory:')
        app.state.artifacts = ArtifactStore(':memory:')
        app.state.snapshots = SnapshotStore(':memory:')

    def tearDown(self):
        try:
            os.remove(self.tmp_session_file.name)
        except OSError:
            pass

    def get(self, path):
        return requests.get(self.url + path, timeout=5)

    def start(self, message='Hello', persona_id=None):
        payload = {'message': message}
        if persona_id is not None:
            payload['persona_id'] = persona_id
        response = requests.post(self.url + '/api/runs', json=payload, timeout=5)
        self.assertEqual(response.status_code, 200)
        return response.json()['run_id']

    def test_index_page_served(self):
        response = self.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertIn('EventSource', response.text)

    def test_personas_endpoint_lists_personas(self):
        response = self.get('/api/personas')
        self.assertEqual(response.status_code, 200)
        personas = response.json()
        ids = {p['id'] for p in personas}
        self.assertIn('explainer', ids)
        self.assertIn('reviewer', ids)

    def test_run_streams_ordered_events_ending_with_done(self):
        run_id = self.start('Hello')
        response = self.get(f'/api/runs/{run_id}/events')
        self.assertTrue(response.headers['content-type'].startswith('text/event-stream'))
        events = parse_sse(response.text)
        self.assertEqual([e['sequence'] for e in events], list(range(len(events))))
        self.assertTrue(all(e['run_id'] == run_id for e in events))
        text = ''.join(e['payload']['text'] for e in events if e['type'] == 'text_delta')
        self.assertEqual(text, 'Stub reply to: Hello')
        self.assertEqual(events[-1]['type'], 'done')
        self.assertEqual(events[-1]['payload']['status'], 'completed')

    def test_run_with_valid_persona_streams_persona_reply(self):
        run_id = self.start('Hello', persona_id='explainer')
        response = self.get(f'/api/runs/{run_id}/events')
        events = parse_sse(response.text)
        text = ''.join(e['payload']['text'] for e in events if e['type'] == 'text_delta')
        self.assertTrue(text.startswith('[Explainer]'))

    def test_run_with_invalid_persona_rejected(self):
        response = requests.post(
            self.url + '/api/runs',
            json={'message': 'Hello', 'persona_id': 'unknown_persona'},
            timeout=5,
        )
        self.assertEqual(response.status_code, 422)

    def test_empty_message_rejected(self):
        self.assertEqual(requests.post(self.url + '/api/runs', json={'message': '  '}, timeout=5).status_code, 422)

    def test_unknown_run_is_404(self):
        self.assertEqual(self.get('/api/runs/nope/events').status_code, 404)

    def test_run_streams_only_once(self):
        run_id = self.start()
        self.get(f'/api/runs/{run_id}/events')
        self.assertEqual(self.get(f'/api/runs/{run_id}/events').status_code, 404)

    def test_cancel_in_flight_run(self):
        app.state.delay = 0.05
        run_id = self.start('One two three four five six seven eight nine ten')
        with requests.get(f'{self.url}/api/runs/{run_id}/events', stream=True, timeout=5) as response:
            cancel_res = requests.post(f'{self.url}/api/runs/{run_id}/cancel', timeout=5)
            self.assertEqual(cancel_res.status_code, 200)
            self.assertEqual(cancel_res.json(), {'run_id': run_id, 'status': 'cancelled'})
            events = parse_sse(response.text)
        self.assertEqual(events[-1]['type'], 'done')
        self.assertEqual(events[-1]['payload']['status'], 'cancelled')

    def test_cancel_before_stream_connects(self):
        run_id = self.start('Hello world')
        cancel_res = requests.post(f'{self.url}/api/runs/{run_id}/cancel', timeout=5)
        self.assertEqual(cancel_res.status_code, 200)
        self.assertEqual(cancel_res.json(), {'run_id': run_id, 'status': 'cancelled'})
        response = self.get(f'/api/runs/{run_id}/events')
        events = parse_sse(response.text)
        self.assertEqual(events[-1]['type'], 'done')
        self.assertEqual(events[-1]['payload']['status'], 'cancelled')

    def test_cancel_unknown_run_is_404(self):
        response = requests.post(f'{self.url}/api/runs/nonexistent/cancel', timeout=5)
        self.assertEqual(response.status_code, 404)

    def test_metrics_endpoint_reflects_runs(self):
        run_id = self.start('Testing metrics')
        self.get(f'/api/runs/{run_id}/events')
        response = self.get('/api/metrics')
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['total_runs'], 1)
        self.assertEqual(data['completed_runs'], 1)
        self.assertEqual(data['failed_runs'], 0)
        self.assertGreater(data['total_tokens'], 0)

    def test_run_detail_endpoint(self):
        run_id = self.start('Testing run detail', persona_id='explainer')
        self.get(f'/api/runs/{run_id}/events')
        response = self.get(f'/api/runs/{run_id}')
        self.assertEqual(response.status_code, 200)
        run = response.json()
        self.assertEqual(run['run_id'], run_id)
        self.assertEqual(run['status'], 'completed')
        self.assertEqual(run['persona_id'], 'explainer')
        self.assertEqual(run['prompt_version'], 'v1')

    def test_list_prompts_and_active_prompt(self):
        res = self.get('/api/prompts')
        self.assertEqual(res.status_code, 200)
        prompts = res.json()
        self.assertTrue(len(prompts) >= 1)
        self.assertEqual(prompts[0]['version_id'], 'v1')

        res_active = self.get('/api/prompts/active')
        self.assertEqual(res_active.status_code, 200)
        self.assertEqual(res_active.json()['version_id'], 'v1')

    def test_create_prompt_version_and_rollback(self):
        create_res = requests.post(
            self.url + '/api/prompts',
            json={'content': 'Updated system prompt content.', 'description': 'test prompt'},
            timeout=5,
        )
        self.assertEqual(create_res.status_code, 200)
        v2 = create_res.json()
        self.assertEqual(v2['version_id'], 'v2')
        self.assertEqual(v2['is_active'], 1)

        # Roll back to v1
        rb_res = requests.post(self.url + '/api/prompts/v1/rollback', timeout=5)
        self.assertEqual(rb_res.status_code, 200)
        self.assertEqual(rb_res.json()['version_id'], 'v1')
        self.assertEqual(rb_res.json()['is_active'], 1)

    def test_compare_prompts_endpoint(self):
        requests.post(
            self.url + '/api/prompts',
            json={'content': 'Updated content for diff.'},
            timeout=5,
        )
        res = self.get('/api/prompts/compare?v1=v1&v2=v2')
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn('diff', data)
        self.assertIn('comparison', data)

    def test_dashboard_page_served(self):
        res = self.get('/dashboard')
        self.assertEqual(res.status_code, 200)
        self.assertIn('Sensai Dashboard', res.text)
        self.assertIn('total-runs', res.text)

    def test_run_history_endpoint(self):
        run_id = self.start('History check')
        self.get(f'/api/runs/{run_id}/events')
        res = self.get(f'/api/runs/{run_id}/history')
        self.assertEqual(res.status_code, 200)
        events = res.json()
        self.assertTrue(len(events) >= 2)
        self.assertEqual(events[0]['run_id'], run_id)

    def test_run_history_unknown_run_is_404(self):
        res = self.get('/api/runs/unknown_run/history')
        self.assertEqual(res.status_code, 404)

    def test_session_persistence_persona_switch_and_clear(self):
        res = requests.post(
            self.url + '/api/runs',
            json={'message': 'First message', 'session_id': 'sess-1'},
            timeout=5,
        )
        self.assertEqual(res.status_code, 200)
        run_id = res.json()['run_id']
        self.get(f'/api/runs/{run_id}/events')

        sess_res = self.get('/api/sessions/sess-1')
        self.assertEqual(sess_res.status_code, 200)
        sess_data = sess_res.json()
        self.assertEqual(sess_data['turns_count'], 2)
        self.assertGreater(sess_data['total_tokens'], 0)

        switch_res = requests.post(
            self.url + '/api/sessions/sess-1/persona',
            json={'persona_id': 'reviewer'},
            timeout=5,
        )
        self.assertEqual(switch_res.status_code, 200)
        self.assertEqual(switch_res.json()['persona_id'], 'reviewer')
        self.assertEqual(switch_res.json()['turns_count'], 2)

        clear_res = requests.post(self.url + '/api/sessions/sess-1/clear', timeout=5)
        self.assertEqual(clear_res.status_code, 200)
        self.assertEqual(self.get('/api/sessions/sess-1').json()['turns_count'], 0)

    def test_artifact_endpoints_and_conflict(self):
        create_res = requests.post(
            self.url + '/api/artifacts',
            json={
                'artifact_id': 'art-1',
                'title': 'System Plan',
                'sections': {'intro': 'V1 intro', 'scope': 'V1 scope'},
            },
            timeout=5,
        )
        self.assertEqual(create_res.status_code, 200)
        self.assertEqual(create_res.json()['version'], 1)

        patch_res = requests.post(
            self.url + '/api/artifacts/art-1/patch',
            json={
                'expected_version': 1,
                'section_id': 'intro',
                'operation': 'update',
                'value': 'V2 intro',
            },
            timeout=5,
        )
        self.assertEqual(patch_res.status_code, 200)
        self.assertEqual(patch_res.json()['version'], 2)
        self.assertEqual(patch_res.json()['sections']['scope'], 'V1 scope')

        conflict_res = requests.post(
            self.url + '/api/artifacts/art-1/patch',
            json={
                'expected_version': 1,
                'section_id': 'intro',
                'operation': 'update',
                'value': 'Should conflict',
            },
            timeout=5,
        )
        self.assertEqual(conflict_res.status_code, 409)

        export_res = self.get('/api/artifacts/art-1/export')
        self.assertEqual(export_res.status_code, 200)
        self.assertIn('# System Plan', export_res.text)
        self.assertIn('V2 intro', export_res.text)

        pdf_res = self.get('/api/artifacts/art-1/export?format=pdf')
        self.assertEqual(pdf_res.status_code, 200)
        self.assertEqual(pdf_res.headers['content-type'], 'application/pdf')
        self.assertTrue(pdf_res.content.startswith(b'%PDF-1.4'))

    def test_forms_validation_and_response_endpoints(self):
        schema = {
            'form_id': 'f-1',
            'title': 'Setup',
            'fields': [{'name': 'port', 'type': 'number'}],
        }
        val_res = requests.post(
            self.url + '/api/forms/validate',
            json={'schema': schema, 'answers': {'port': '8080'}},
            timeout=5,
        )
        self.assertEqual(val_res.status_code, 200)
        self.assertEqual(val_res.json()['answers'], {'port': 8080})

        run_id = self.start('Form run')
        form_res = requests.post(
            f'{self.url}/api/runs/{run_id}/form_response',
            json={'answers': {'port': 8080}},
            timeout=5,
        )
        self.assertEqual(form_res.status_code, 200)
        self.assertEqual(form_res.json()['status'], 'resumed')

    def test_form_request_pause_validation_and_resume_sse(self):
        res = requests.post(
            self.url + '/api/runs',
            json={'message': 'clarify: build parser'},
            timeout=5,
        )
        self.assertEqual(res.status_code, 200)
        run_id = res.json()['run_id']

        events_out = []

        def read_sse():
            resp = self.get(f'/api/runs/{run_id}/events')
            events_out.append(resp.text)

        reader = threading.Thread(target=read_sse, daemon=True)
        reader.start()

        for _ in range(50):
            r = app.state.runs.get(run_id)
            if r and r.get('form_waiting'):
                break
            time.sleep(0.02)

        bad_res = requests.post(
            f'{self.url}/api/runs/{run_id}/form_response',
            json={'answers': {'language': 'python', 'include_tests': 'yes', 'max_steps': 'abc'}},
            timeout=5,
        )
        self.assertEqual(bad_res.status_code, 422)

        good_res = requests.post(
            f'{self.url}/api/runs/{run_id}/form_response',
            json={'answers': {'language': 'python', 'include_tests': 'yes', 'max_steps': '3'}},
            timeout=5,
        )
        self.assertEqual(good_res.status_code, 200)
        self.assertEqual(
            good_res.json()['received'],
            {'language': 'python', 'include_tests': True, 'max_steps': 3},
        )

        reader.join(timeout=5)
        self.assertEqual(len(events_out), 1)
        events = parse_sse(events_out[0])
        event_types = [e['type'] for e in events]
        self.assertIn('form_request', event_types)
        self.assertEqual(events[-1]['payload']['status'], 'completed')
        full_text = ''.join(e['payload']['text'] for e in events if e['type'] == 'text_delta')
        self.assertIn('"max_steps": 3', full_text)

    def test_snapshots_and_public_export_endpoints(self):
        snap_res = requests.post(
            self.url + '/api/snapshots',
            json={
                'title': 'Public Demo',
                'type': 'conversation',
                'data': {
                    'internal_secret': 'do-not-leak',
                    'messages': [
                        {'role': 'user', 'content': 'Hi'},
                        {'role': 'assistant', 'content': 'Hello there!'},
                    ],
                },
                'custom_id': 'pub-demo-1',
            },
            timeout=5,
        )
        self.assertEqual(snap_res.status_code, 200)

        pub_page = self.get('/p/pub-demo-1')
        self.assertEqual(pub_page.status_code, 200)
        self.assertIn('Public Demo', pub_page.text)
        self.assertNotIn('do-not-leak', pub_page.text)

        md_res = self.get('/p/pub-demo-1/export?format=md')
        self.assertEqual(md_res.status_code, 200)
        self.assertIn('# Public Demo', md_res.text)

        pdf_res = self.get('/p/pub-demo-1/export?format=pdf')
        self.assertEqual(pdf_res.status_code, 200)
        self.assertEqual(pdf_res.headers['content-type'], 'application/pdf')
        self.assertTrue(pdf_res.content.startswith(b'%PDF-1.4'))
        self.assertIn(b'Public Demo', pdf_res.content)


if __name__ == '__main__':
    unittest.main()

