"""Tests for the web UI document-upload and RAG ingestion endpoint."""
import asyncio
from io import BytesIO
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from starlette.datastructures import UploadFile

from retrieval.store import VectorStore
from web import app as web_module


class DocumentUploadTests(unittest.TestCase):
    def setUp(self):
        self.app = web_module.app
        self.old_store = self.app.state.vector_store
        self.old_guardrails = self.app.state.guardrails
        self.store = VectorStore(":memory:")
        self.app.state.vector_store = self.store
        self.app.state.guardrails = None
        self.addCleanup(self._restore_state)

    def _restore_state(self):
        self.app.state.vector_store = self.old_store
        self.app.state.guardrails = self.old_guardrails
        self.store.close()

    @staticmethod
    def upload(filename, content):
        return UploadFile(filename=filename, file=BytesIO(content))

    def test_uploads_and_indexes_markdown(self):
        document = self.upload(
            "guide.md",
            b"SensAI uses retrieval augmented generation.\nDocuments are split into chunks.",
        )
        with patch("web.app.embed", return_value=[[1.0, 0.0]]):
            result = asyncio.run(web_module.upload_retrieval_document(document))

        self.assertEqual(result["status"], "ingested")
        self.assertEqual(result["filename"], "guide.md")
        self.assertEqual(result["chunks_count"], 1)
        chunks = self.store.get_all()
        self.assertEqual(len(chunks), 1)
        self.assertIn("retrieval augmented generation", chunks[0]["content"])

    def test_upload_uses_requested_collection_configuration(self):
        self.store.create_collection("course")
        document = self.upload("guide.md", b"one\ntwo\nthree")
        with patch("web.app.embed", return_value=[[1.0, 0.0]]) as embed_mock:
            result = asyncio.run(web_module.upload_retrieval_document(
                document,
                collection_id="course",
                embedding_model="course-embedder",
                chunk_size=2,
                chunk_overlap=0,
            ))

        self.assertEqual(result["collection_id"], "course")
        self.assertEqual(embed_mock.call_args.kwargs["model"], "course-embedder")
        config = self.store.get_collection("course")
        self.assertEqual(config["chunk_size"], 2)
        self.assertEqual(config["chunk_overlap"], 0)

    def test_upload_reports_embedding_failure_without_fallback(self):
        document = self.upload("guide.md", b"content")
        with patch("web.app.embed", side_effect=RuntimeError("Ollama is unavailable")):
            with self.assertRaises(HTTPException) as raised:
                asyncio.run(web_module.upload_retrieval_document(document))

        self.assertEqual(raised.exception.status_code, 502)
        self.assertEqual(self.store.get_all(), [])

    def test_duplicate_upload_does_not_duplicate_chunks(self):
        content = b"same document content"
        with patch("web.app.embed", return_value=[[1.0, 0.0]]):
            first = asyncio.run(web_module.upload_retrieval_document(
                self.upload("guide.md", content)
            ))
            second = asyncio.run(web_module.upload_retrieval_document(
                self.upload("guide.md", content)
            ))

        self.assertEqual(first["chunks_count"], 1)
        self.assertEqual(second["chunks_count"], 1)
        self.assertEqual(len(self.store.get_all()), 1)

    def test_rejects_unsupported_extension(self):
        with self.assertRaises(HTTPException) as raised:
            asyncio.run(web_module.upload_retrieval_document(
                self.upload("secret.pdf", b"not supported")
            ))
        self.assertEqual(raised.exception.status_code, 415)

    def test_rejects_empty_document(self):
        with self.assertRaises(HTTPException) as raised:
            asyncio.run(web_module.upload_retrieval_document(
                self.upload("empty.txt", b"  \n\t")
            ))
        self.assertEqual(raised.exception.status_code, 422)

    def test_rejects_non_utf8_document(self):
        with self.assertRaises(HTTPException) as raised:
            asyncio.run(web_module.upload_retrieval_document(
                self.upload("bad.txt", b"\xff\xfe\xfa")
            ))
        self.assertEqual(raised.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main()
