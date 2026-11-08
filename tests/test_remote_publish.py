import os
import unittest
from unittest.mock import MagicMock, patch

import requests

from export.remote import RemotePublishError, publish_remote, remote_config
from export.service import SnapshotStore

ENV = {'SENSAI_PUBLISH_URL': 'https://pub.example.vercel.app/', 'SENSAI_PUBLISH_TOKEN': 'tok'}


def reply(status, body=None):
    response = MagicMock()
    response.status_code = status
    response.ok = status < 400
    response.json.return_value = body or {}
    return response


class RemotePublishTests(unittest.TestCase):
    def setUp(self):
        self.store = SnapshotStore()
        self.store.publish_snapshot('Demo', 'conversation',
                                    {'messages': [{'role': 'user', 'content': 'Hi'}]}, custom_id='snap-abc123')

    def test_config_needs_url_and_token(self):
        with patch.dict(os.environ, {'SENSAI_PUBLISH_URL': '', 'SENSAI_PUBLISH_TOKEN': ''}), patch('export.remote._local_settings', return_value={}):
            self.assertIsNone(remote_config())
        with patch.dict(os.environ, {'SENSAI_PUBLISH_URL': 'ftp://x', 'SENSAI_PUBLISH_TOKEN': 't'}):
            self.assertIsNone(remote_config())
        with patch.dict(os.environ, ENV):
            self.assertEqual(remote_config(), ('https://pub.example.vercel.app', 'tok'))

    def test_uploads_rendered_files_with_token(self):
        with patch.dict(os.environ, ENV), patch('export.remote.requests.post',
                                                 return_value=reply(200, {'url': 'https://pub.example.vercel.app/p/snap-abc123'})) as post:
            url = publish_remote(self.store, 'snap-abc123')
        self.assertEqual(url, 'https://pub.example.vercel.app/p/snap-abc123')
        args, kwargs = post.call_args
        self.assertEqual(args[0], 'https://pub.example.vercel.app/api/publish')
        self.assertEqual(kwargs['headers']['Authorization'], 'Bearer tok')
        body = kwargs['json']
        self.assertEqual(body['id'], 'snap-abc123')
        self.assertIn('<!doctype html>', body['html'])
        self.assertNotIn('/p/snap-abc123/export', body['html_download'])
        self.assertTrue(body['pdf_base64'])

    def test_failures_raise_clear_errors(self):
        with patch.dict(os.environ, ENV):
            with patch('export.remote.requests.post', side_effect=requests.ConnectionError()):
                with self.assertRaisesRegex(RemotePublishError, 'internet'):
                    publish_remote(self.store, 'snap-abc123')
            with patch('export.remote.requests.post', return_value=reply(401)):
                with self.assertRaisesRegex(RemotePublishError, 'token'):
                    publish_remote(self.store, 'snap-abc123')
            with patch('export.remote.requests.post', return_value=reply(200, {'url': 'javascript:alert(1)'})):
                with self.assertRaises(RemotePublishError):
                    publish_remote(self.store, 'snap-abc123')


if __name__ == '__main__':
    unittest.main()
