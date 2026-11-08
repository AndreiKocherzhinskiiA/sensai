import tempfile
import threading
import time
import os
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from core.chat import execute_turn_stream, semantic_cache_key
from retrieval.semantic_cache import SemanticCache


class SemanticCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp_dir.name) / "cache.sqlite3")
        self.cache = SemanticCache(
            db_path=self.db_path,
            threshold=0.90,
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    @patch.object(SemanticCache, "_embed")
    def test_cache_hit_for_similar_query(self, mock_embed):
        mock_embed.side_effect = [
            [1.0, 0.0],
            [0.99, 0.01],
        ]

        self.cache.put(
            "How do I reset my password?",
            "Use the password reset page.",
            "support-context",
        )

        response, similarity, query_embedding = self.cache.get(
            "How can I reset my password?",
            "support-context",
        )

        self.assertEqual(response, "Use the password reset page.")
        self.assertGreaterEqual(similarity, 0.90)
        self.assertEqual(query_embedding, [0.99, 0.01])

    @patch.object(SemanticCache, "_embed")
    def test_cache_miss_for_dissimilar_query(self, mock_embed):
        mock_embed.side_effect = [
            [1.0, 0.0],
            [0.0, 1.0],
        ]

        self.cache.put(
            "How do I reset my password?",
            "Use the password reset page.",
            "support-context",
        )

        response, similarity, query_embedding = self.cache.get(
            "What are your opening hours?",
            "support-context",
        )

        self.assertIsNone(response)
        self.assertEqual(similarity, 0.0)
        self.assertEqual(query_embedding, [0.0, 1.0])

    @patch.object(SemanticCache, "_embed", return_value=[1.0, 0.0])
    def test_cache_is_isolated_by_context(self, mock_embed):
        self.cache.put(
            "How do I reset my password?",
            "Use the password reset page.",
            "context-a",
        )

        response, similarity, query_embedding = self.cache.get(
            "How do I reset my password?",
            "context-b",
        )

        self.assertIsNone(response)
        self.assertEqual(similarity, 0.0)
        self.assertEqual(query_embedding, [1.0, 0.0])

    @patch.object(SemanticCache, "_embed", return_value=[1.0, 0.0])
    def test_cache_persists_across_instances(self, mock_embed):
        self.cache.put(
            "How do I reset my password?",
            "Use the password reset page.",
            "support-context",
        )

        second_cache = SemanticCache(
            db_path=self.db_path,
            threshold=0.90,
        )

        response, similarity, query_embedding = second_cache.get(
            "How do I reset my password?",
            "support-context",
        )

        self.assertEqual(response, "Use the password reset page.")
        self.assertGreaterEqual(similarity, 0.90)
        self.assertEqual(query_embedding, [1.0, 0.0])

    @patch.object(SemanticCache, "_embed", return_value=[1.0, 0.0])
    def test_expired_entries_are_not_reused_and_are_cleaned_up(self, mock_embed):
        cache = SemanticCache(self.db_path, max_age_seconds=0.01)
        cache.put("Question", "Old answer", "context")
        time.sleep(0.02)

        response, _, _ = cache.get("Question", "context")

        self.assertIsNone(response)
        self.assertEqual(cache.stats()["entries"], 0)

    @patch.object(SemanticCache, "_embed", return_value=[1.0, 0.0])
    def test_malformed_or_wrong_dimension_embeddings_are_cache_misses(self, mock_embed):
        self.cache.put("Question", "Answer", "context")
        with self.cache._connect() as connection:
            connection.execute("UPDATE semantic_cache SET embedding = ?", ('[1.0]',))

        response, _, _ = self.cache.get("Question", "context")

        self.assertIsNone(response)


class SemanticCacheIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.messages = [
            {
                "role": "system",
                "content": "You are a support assistant.",
            },
            {
                "role": "user",
                "content": "How do I reset my password?",
            },
        ]

    def test_cache_hit_skips_model_call(self):
        fake_cache = Mock()
        fake_cache.get.return_value = (
            "Use the password reset page.",
            1.0,
            [1.0, 0.0],
        )

        with patch(
            "core.chat.get_semantic_cache",
            return_value=fake_cache,
        ):
            with patch("core.chat.stream_reply") as mock_stream:
                result = list(
                    execute_turn_stream(
                        host="http://localhost:11434",
                        model="test-model",
                        to_send=self.messages,
                        timeout=10,
                    )
                )

        self.assertEqual(
            result,
            ["Use the password reset page."],
        )
        mock_stream.assert_not_called()
        fake_cache.put.assert_not_called()

    @patch('core.chat.SemanticCache')
    def test_cache_uses_turn_host_for_embeddings(self, cache_class):
        import core.chat as chat

        old_cache = chat._semantic_cache
        old_config = chat._semantic_cache_config
        chat._semantic_cache = None
        chat._semantic_cache_config = None
        try:
            chat.get_semantic_cache('http://ollama.internal:11434')
        finally:
            chat._semantic_cache = old_cache
            chat._semantic_cache_config = old_config

        self.assertEqual(
            cache_class.call_args.kwargs['base_url'],
            'http://ollama.internal:11434',
        )

    def test_cache_singleton_rebuilds_for_every_runtime_configuration_change(self):
        import core.chat as chat

        original_cache = chat._semantic_cache
        original_config = chat._semantic_cache_config
        cache_instances = [object() for _ in range(7)]
        environment = {
            "SENSAI_SEMANTIC_CACHE_ENABLED": "true",
            "SENSAI_SEMANTIC_CACHE_DB": "/tmp/cache-a.sqlite3",
            "SENSAI_SEMANTIC_CACHE_THRESHOLD": "0.90",
            "SENSAI_SEMANTIC_CACHE_EMBEDDING_MODEL": "embed-a",
            "SENSAI_SEMANTIC_CACHE_MAX_AGE_SECONDS": "60",
        }

        chat._semantic_cache = None
        chat._semantic_cache_config = None
        try:
            with patch.dict(os.environ, environment, clear=False), patch(
                "core.chat.SemanticCache", side_effect=cache_instances
            ) as cache_class:
                first = chat.get_semantic_cache("http://host-a:11434")
                self.assertIs(first, chat.get_semantic_cache("http://host-a:11434"))

                os.environ["SENSAI_SEMANTIC_CACHE_DB"] = "/tmp/cache-b.sqlite3"
                second = chat.get_semantic_cache("http://host-a:11434")
                os.environ["SENSAI_SEMANTIC_CACHE_THRESHOLD"] = "0.95"
                third = chat.get_semantic_cache("http://host-a:11434")
                os.environ["SENSAI_SEMANTIC_CACHE_EMBEDDING_MODEL"] = "embed-b"
                fourth = chat.get_semantic_cache("http://host-a:11434")
                os.environ["SENSAI_SEMANTIC_CACHE_MAX_AGE_SECONDS"] = "120"
                fifth = chat.get_semantic_cache("http://host-a:11434")
                sixth = chat.get_semantic_cache("http://host-b:11434")

                os.environ["SENSAI_SEMANTIC_CACHE_ENABLED"] = "false"
                self.assertIsNone(chat.get_semantic_cache("http://host-b:11434"))
                os.environ["SENSAI_SEMANTIC_CACHE_ENABLED"] = "true"
                seventh = chat.get_semantic_cache("http://host-b:11434")
        finally:
            chat._semantic_cache = original_cache
            chat._semantic_cache_config = original_config

        self.assertEqual(
            [first, second, third, fourth, fifth, sixth, seventh],
            cache_instances,
        )
        self.assertEqual(cache_class.call_count, 7)
        self.assertEqual(cache_class.call_args_list[1].kwargs["db_path"], "/tmp/cache-b.sqlite3")
        self.assertEqual(cache_class.call_args_list[2].kwargs["threshold"], 0.95)
        self.assertEqual(cache_class.call_args_list[3].kwargs["embedding_model"], "embed-b")
        self.assertEqual(cache_class.call_args_list[4].kwargs["max_age_seconds"], 120.0)
        self.assertEqual(cache_class.call_args_list[5].kwargs["base_url"], "http://host-b:11434")

    def test_cache_miss_calls_model_and_stores_response(self):
        fake_cache = Mock()
        fake_cache.get.return_value = (None, 0.0, [1.0, 0.0])

        with patch(
            "core.chat.get_semantic_cache",
            return_value=fake_cache,
        ):
            with patch(
                "core.chat.stream_reply",
                return_value=iter(["Use ", "the reset page."]),
            ) as mock_stream:
                result = list(
                    execute_turn_stream(
                        host="http://localhost:11434",
                        model="test-model",
                        to_send=self.messages,
                        timeout=10,
                    )
                )

        self.assertEqual(result, ["Use ", "the reset page."])
        mock_stream.assert_called_once()

        fake_cache.put.assert_called_once()
        stored_args = fake_cache.put.call_args.args

        self.assertEqual(
            stored_args[0],
            "How do I reset my password?",
        )
        self.assertEqual(
            stored_args[1],
            "Use the reset page.",
        )
        self.assertTrue(stored_args[2])

    def test_cache_key_changes_with_model_or_system_context(self):
        base = semantic_cache_key("http://localhost:11434", "model-a", self.messages)
        other_model = semantic_cache_key("http://localhost:11434", "model-b", self.messages)
        other_system = semantic_cache_key(
            "http://localhost:11434",
            "model-a",
            [
                {"role": "system", "content": "You are a different assistant."},
                self.messages[-1],
            ],
        )

        self.assertNotEqual(base, other_model)
        self.assertNotEqual(base, other_system)

    def test_stop_event_allows_cache_when_not_cancelled(self):
        fake_cache = Mock()
        fake_cache.get.return_value = ("Cached response.", 1.0, [1.0, 0.0])

        with patch("core.chat.get_semantic_cache", return_value=fake_cache), patch(
            "core.chat.stream_reply"
        ) as model:
            result = list(
                execute_turn_stream(
                    host="http://localhost:11434",
                    model="test-model",
                    to_send=self.messages,
                    timeout=10,
                    stop_event=threading.Event(),
                )
            )

        self.assertEqual(result, ["Cached response."])
        model.assert_not_called()

    def test_context_dependent_or_guarded_requests_bypass_cache(self):
        fake_cache = Mock()
        model = Mock(return_value=iter(["Fresh response."]))
        history = self.messages[:-1] + [
            {"role": "user", "content": "Earlier question"},
            {"role": "assistant", "content": "Earlier answer"},
            self.messages[-1],
        ]

        with patch("core.chat.get_semantic_cache", return_value=fake_cache):
            list(
                execute_turn_stream(
                    "http://localhost:11434", "test-model", history, 10,
                    stream_func=model,
                )
            )
            list(
                execute_turn_stream(
                    "http://localhost:11434", "test-model", self.messages, 10,
                    stream_func=model, cache_context_independent=False,
                )
            )

        fake_cache.get.assert_not_called()
        self.assertEqual(model.call_count, 2)

    def test_all_context_dependent_turn_kinds_bypass_cache(self):
        fake_cache = Mock()
        model = Mock(return_value=iter(["Fresh response."]))
        # The CLI marks each of these request kinds as context-dependent before
        # calling the shared executor; it must never consult the cache.
        for kind in ("rag", "attachment", "mcp", "search", "guarded", "structured"):
            with patch("core.chat.get_semantic_cache", return_value=fake_cache):
                list(execute_turn_stream(
                    "http://localhost:11434", "test-model", self.messages, 10,
                    stream_func=model, cache_context_independent=False,
                ))
        fake_cache.get.assert_not_called()
        self.assertEqual(model.call_count, 6)

    def test_cache_error_falls_back_to_model_generation(self):
        fake_cache = Mock()
        fake_cache.get.side_effect = RuntimeError("embedding unavailable")
        model = Mock(return_value=iter(["Fresh response."]))

        with patch("core.chat.get_semantic_cache", return_value=fake_cache):
            result = list(
                execute_turn_stream(
                    "http://localhost:11434", "test-model", self.messages, 10,
                    stream_func=model,
                )
            )

        self.assertEqual(result, ["Fresh response."])
        model.assert_called_once()

    def test_cache_write_error_does_not_break_model_response(self):
        fake_cache = Mock()
        fake_cache.get.return_value = (None, 0.0, [1.0, 0.0])
        fake_cache.put.side_effect = RuntimeError("database locked")
        model = Mock(return_value=iter(["Fresh response."]))

        with patch("core.chat.get_semantic_cache", return_value=fake_cache):
            result = list(execute_turn_stream(
                "http://localhost:11434", "test-model", self.messages, 10,
                stream_func=model,
            ))

        self.assertEqual(result, ["Fresh response."])
        model.assert_called_once()

    def test_disabled_cache_skips_cache_calls(self):
        model = Mock(return_value=iter(["Fresh response."]))
        with patch.dict(os.environ, {"SENSAI_SEMANTIC_CACHE_ENABLED": "false"}), patch(
            "core.chat.stream_reply", model
        ), patch("core.chat.get_semantic_cache", return_value=None) as cache_get:
            result = list(execute_turn_stream(
                "http://localhost:11434", "test-model", self.messages, 10,
            ))

        self.assertEqual(result, ["Fresh response."])
        cache_get.assert_called_once()



if __name__ == "__main__":
    unittest.main()
