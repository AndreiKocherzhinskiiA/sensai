"""Tests for the retrieval integration in chatbot.run_chat."""
import contextlib
import io
import json
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

import chatbot
from retrieval.store import VectorStore


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        data = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        self.server.payloads.append(data)
        if self.path == '/api/generate':
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(b'{"response":"8"}')
            return
        status, chunks = self.server.replies.pop(0)
        self.send_response(status)
        self.send_header('Content-Type', 'application/x-ndjson')
        self.end_headers()
        for chunk in chunks:
            self.wfile.write((json.dumps(chunk, ensure_ascii=False) + '\n').encode())
            self.wfile.flush()


def fake_embed(texts, model='nomic-embed-text'):
    """Deterministic fake embeddings: 'cat' texts point one way, else another."""
    vectors = []
    for text in texts:
        vectors.append([1.0, 0.0] if 'cat' in text.lower() else [0.0, 1.0])
    return vectors


class RetrievalIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.payloads = []
        self.server.replies = []
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f'http://127.0.0.1:{self.server.server_port}'
        self.store = VectorStore(':memory:')

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.store.close()

    def reply(self, text='Hello'):
        self.server.replies.append((200, [
            {'message': {'content': text}, 'done': False},
            {'message': {'content': ''}, 'done': True},
        ]))

    def run_cli(self, inputs, store=None):
        output = io.StringIO()
        with patch('builtins.input', side_effect=inputs), \
                patch.dict(os.environ, {'SENSAI_SEMANTIC_CACHE_ENABLED': 'false'}), \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            chatbot.run_chat('test-model', 'System prompt', self.url, 2, store=store)
        return output.getvalue()

    def test_store_none_behaves_exactly_like_before(self):
        self.reply('Noted')
        self.run_cli(['Hello', '/exit'], store=None)
        self.assertEqual(self.server.payloads[0]['messages'], [
            {'role': 'system', 'content': 'System prompt'},
            {'role': 'user', 'content': 'Hello'},
        ])

    @patch('retrieval.rag.embed', side_effect=fake_embed)
    @patch('retrieval.semantic_cache.embed', side_effect=fake_embed)
    def test_matching_document_injects_context_before_user_turn(
            self, mock_cache_embed, mock_embed):
        self.store.add('cats.md', 1, 1, 'All about cats.', [1.0, 0.0])
        self.reply('Answer about cats')

        self.run_cli(['tell me about cats', '/exit'], store=self.store)

        sent_messages = next(
            payload['messages']
            for payload in self.server.payloads
            if 'messages' in payload
        )
        # system prompt, then the user's question with the shared context block
        self.assertEqual(sent_messages[0]['role'], 'system')
        self.assertEqual(sent_messages[0]['content'], 'System prompt')
        self.assertEqual(sent_messages[1]['role'], 'user')
        self.assertIn('<retrieved_context>', sent_messages[1]['content'])
        self.assertIn('cats.md', sent_messages[1]['content'])
        self.assertIn('All about cats.', sent_messages[1]['content'])
        self.assertIn('tell me about cats', sent_messages[1]['content'])

    @patch('retrieval.rag.embed', side_effect=RuntimeError('Ollama unreachable'))
    def test_retrieval_failure_does_not_crash_chat(self, mock_embed):
        self.reply('Still answered')
        output = self.run_cli(['hello there', '/exit'], store=self.store)

        self.assertIn('Retrieval unavailable', output)
        # chat still completed and sent the user's message normally
        sent_messages = next(
            payload['messages']
            for payload in self.server.payloads
            if 'messages' in payload
        )
        self.assertEqual(sent_messages[-1]['content'], 'hello there')

    @patch('retrieval.rag.embed', side_effect=fake_embed)
    @patch('retrieval.semantic_cache.embed', side_effect=fake_embed)
    def test_unmatched_question_abstains_without_calling_model(
            self, mock_cache_embed, mock_embed):
        self.store.add('cats.md', 1, 1, 'All about cats.', [1.0, 0.0])

        output = self.run_cli(['What is the capital of Mars?', '/exit'],
                              store=self.store)

        self.assertIn(
            "I couldn't find relevant information in the indexed documents",
            output,
        )
        self.assertNotIn('What is the capital of Mars?', self.server.payloads)


if __name__ == '__main__':
    unittest.main()
