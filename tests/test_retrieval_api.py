"""Tests for collection management and retrieval inspection endpoints."""
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from retrieval.store import VectorStore
from web import app as web_module


class RetrievalApiTests(unittest.TestCase):
    def setUp(self):
        self.app = web_module.app
        self.old_store = self.app.state.vector_store
        self.store = VectorStore(":memory:")
        self.app.state.vector_store = self.store
        self.addCleanup(self._restore_state)

    def _restore_state(self):
        self.app.state.vector_store = self.old_store
        self.store.close()

    def test_collection_crud_and_chunk_inspection(self):
        collection = web_module.create_retrieval_collection(
            web_module.CollectionRequest(
                collection_id="course",
                embedding_model="test-embedder",
                chunk_size=4,
                chunk_overlap=1,
                relevance_threshold=0.8,
            )
        )
        self.assertEqual(collection["id"], "course")

        updated = web_module.update_retrieval_collection(
            "course",
            web_module.CollectionUpdateRequest(relevance_threshold=0.9),
        )
        self.assertEqual(updated["relevance_threshold"], 0.9)

        self.store.add(
            "notes.md", 1, 2, "course notes", [1.0, 0.0],
            collection_id="course",
        )
        chunks = web_module.list_retrieval_chunks("course")
        self.assertEqual(chunks[0]["source_file"], "notes.md")

        deleted = web_module.delete_retrieval_collection("course")
        self.assertEqual(deleted["status"], "deleted")

    @patch("retrieval.rag.embed", return_value=[[1.0, 0.0]])
    def test_search_endpoint_returns_scores_and_collection(self, embed_mock):
        self.store.add(
            "notes.md", 1, 1, "course notes", [1.0, 0.0],
            collection_id="default",
        )
        result = web_module.search_retrieval(
            web_module.RetrievalSearchRequest(query="course", top_k=1)
        )

        self.assertEqual(result["collection_id"], "default")
        self.assertEqual(result["accepted_count"], 1)
        self.assertEqual(result["results"][0]["score"], 1.0)
        embed_mock.assert_called_once()

    def test_search_rejects_empty_query(self):
        with self.assertRaises(HTTPException) as raised:
            web_module.search_retrieval(
                web_module.RetrievalSearchRequest(query=" ")
            )
        self.assertEqual(raised.exception.status_code, 422)

    def test_invalid_collection_configuration_is_rejected(self):
        with self.assertRaises(HTTPException) as raised:
            web_module.create_retrieval_collection(
                web_module.CollectionRequest(
                    collection_id="invalid",
                    chunk_size=2,
                    chunk_overlap=2,
                )
            )
        self.assertEqual(raised.exception.status_code, 422)

    def test_collection_search_isolated_from_default_collection(self):
        self.store.create_collection("other")
        self.store.add(
            "other.md", 1, 1, "other collection", [1.0, 0.0],
            collection_id="other",
        )

        with patch("retrieval.rag.embed", return_value=[[1.0, 0.0]]):
            result = web_module.search_retrieval(
                web_module.RetrievalSearchRequest(
                    query="other", collection_id="default"
                )
            )

        self.assertEqual(result["accepted_count"], 0)


if __name__ == "__main__":
    unittest.main()
