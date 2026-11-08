"""ChromaDB-backed vector storage with SQLite collection configuration."""
import hashlib
import json
import math
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import chromadb
from chromadb.config import Settings
from chromadb.errors import InvalidDimensionException


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two equal-length vectors."""
    if len(a) != len(b):
        raise ValueError("vectors must be the same length")

    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


class VectorStore:
    """ChromaDB vector store with collection-scoped configuration."""

    def __init__(self, db_path: str = ":memory:"):
        self._persistent = db_path != ":memory:"
        if self._persistent:
            chroma_path = str(Path(f"{db_path}.chroma"))
            config_path = f"{db_path}.config.sqlite3"
            self._tempdir = None
            self.client = chromadb.PersistentClient(
                path=chroma_path,
                settings=Settings(anonymized_telemetry=False),
            )
        else:
            config_path = ":memory:"
            self._tempdir = tempfile.TemporaryDirectory(prefix="sensai-chroma-")
            self.client = chromadb.PersistentClient(
                path=self._tempdir.name,
                settings=Settings(anonymized_telemetry=False),
            )

        self.conn = sqlite3.connect(config_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS collections (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                embedding_model TEXT NOT NULL,
                embedding_dimension INTEGER,
                chunk_size INTEGER NOT NULL,
                chunk_overlap INTEGER NOT NULL,
                similarity_metric TEXT NOT NULL,
                relevance_threshold REAL NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        self._ensure_default_collection()
        self.client.get_or_create_collection(
            name="default",
            metadata={"hnsw:space": "cosine"},
        )
        self.conn.commit()

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _ensure_default_collection(self):
        now = self._now()
        self.conn.execute(
            """
            INSERT OR IGNORE INTO collections (
                id, name, embedding_model, embedding_dimension, chunk_size,
                chunk_overlap, similarity_metric, relevance_threshold,
                created_at, updated_at
            ) VALUES ('default', 'default', 'nomic-embed-text', NULL, 20, 5,
                      'cosine', 0.55, ?, ?)
            """,
            (now, now),
        )

    @staticmethod
    def _validate_config(
        embedding_model: str,
        chunk_size: int,
        chunk_overlap: int,
        similarity_metric: str,
        relevance_threshold: float,
    ):
        if not embedding_model.strip():
            raise ValueError("embedding_model must not be empty")
        if chunk_size <= 0 or not 0 <= chunk_overlap < chunk_size:
            raise ValueError("chunk_overlap must be non-negative and smaller than chunk_size")
        if similarity_metric != "cosine":
            raise ValueError("similarity_metric must be 'cosine'")
        if not 0.0 <= relevance_threshold <= 1.0:
            raise ValueError("relevance_threshold must be between 0 and 1")

    def create_collection(
        self,
        collection_id: str,
        *,
        name: str | None = None,
        embedding_model: str = "nomic-embed-text",
        chunk_size: int = 20,
        chunk_overlap: int = 5,
        similarity_metric: str = "cosine",
        relevance_threshold: float = 0.7,
    ) -> dict:
        """Create a Chroma collection and its configuration."""
        if not collection_id.strip():
            raise ValueError("collection_id must not be empty")
        self._validate_config(
            embedding_model, chunk_size, chunk_overlap,
            similarity_metric, relevance_threshold,
        )
        if self.conn.execute(
            "SELECT 1 FROM collections WHERE id = ?", (collection_id,)
        ).fetchone():
            raise ValueError(f"Collection already exists: {collection_id}")
        now = self._now()
        try:
            self.client.create_collection(
                name=collection_id,
                metadata={"hnsw:space": "cosine"},
            )
            self.conn.execute(
                """
                INSERT INTO collections (
                    id, name, embedding_model, embedding_dimension, chunk_size,
                    chunk_overlap, similarity_metric, relevance_threshold,
                    created_at, updated_at
                ) VALUES (?, ?, ?, NULL, ?, ?, ?, ?, ?, ?)
                """,
                (
                    collection_id, name or collection_id, embedding_model,
                    chunk_size, chunk_overlap, similarity_metric,
                    relevance_threshold, now, now,
                ),
            )
            self.conn.commit()
        except sqlite3.IntegrityError as error:
            self.client.delete_collection(collection_id)
            raise ValueError(f"Collection already exists: {collection_id}") from error
        return self.get_collection(collection_id)

    def _chroma_collection(self, collection_id: str):
        self.get_collection(collection_id)
        return self.client.get_collection(collection_id)

    def list_collections(self) -> list[dict]:
        """Return all collection configurations."""
        rows = self.conn.execute("SELECT * FROM collections ORDER BY name").fetchall()
        return [dict(row) for row in rows]

    def get_collection(self, collection_id: str = "default") -> dict:
        """Return one collection configuration."""
        row = self.conn.execute(
            "SELECT * FROM collections WHERE id = ?", (collection_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"Unknown collection: {collection_id}")
        return dict(row)

    def update_collection(self, collection_id: str, **updates) -> dict:
        """Update collection configuration without changing vector data."""
        current = self.get_collection(collection_id)
        allowed = {
            "name", "embedding_model", "chunk_size", "chunk_overlap",
            "similarity_metric", "relevance_threshold",
        }
        unknown = set(updates) - allowed
        if unknown:
            raise ValueError(f"Unknown collection fields: {sorted(unknown)}")
        values = {key: updates.get(key, current[key]) for key in allowed}
        if not values["name"].strip():
            raise ValueError("name must not be empty")
        self._validate_config(
            values["embedding_model"], values["chunk_size"],
            values["chunk_overlap"], values["similarity_metric"],
            values["relevance_threshold"],
        )
        try:
            self.conn.execute(
                """
                UPDATE collections
                SET name = ?, embedding_model = ?, chunk_size = ?, chunk_overlap = ?,
                    similarity_metric = ?, relevance_threshold = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    values["name"], values["embedding_model"], values["chunk_size"],
                    values["chunk_overlap"], values["similarity_metric"],
                    values["relevance_threshold"], self._now(), collection_id,
                ),
            )
            self.conn.commit()
        except sqlite3.IntegrityError as error:
            raise ValueError(f"Collection name already exists: {values['name']}") from error
        return self.get_collection(collection_id)

    def delete_collection(self, collection_id: str):
        """Delete a Chroma collection and its configuration."""
        if collection_id == "default":
            raise ValueError("The default collection cannot be deleted")
        self.get_collection(collection_id)
        self.client.delete_collection(collection_id)
        self.conn.execute("DELETE FROM collections WHERE id = ?", (collection_id,))
        self.conn.commit()

    def add(
        self,
        source_file: str,
        start_line: int,
        end_line: int,
        content: str,
        embedding: list[float],
        metadata: dict | None = None,
        collection_id: str = "default",
    ) -> str:
        """Insert one chunk into a Chroma collection."""
        collection = self.get_collection(collection_id)
        dimension = collection["embedding_dimension"]
        if dimension is not None and dimension != len(embedding):
            raise ValueError(
                f"Embedding dimension {len(embedding)} does not match collection dimension {dimension}"
            )
        if dimension is None:
            self.conn.execute(
                "UPDATE collections SET embedding_dimension = ?, updated_at = ? WHERE id = ?",
                (len(embedding), self._now(), collection_id),
            )
            self.conn.commit()

        chunk_id = hashlib.sha256(
            f"{source_file}:{start_line}:{end_line}:{content}".encode("utf-8")
        ).hexdigest()
        stored_metadata: dict[str, Any] = dict(metadata or {})
        stored_metadata.update({
            "source_file": source_file,
            "start_line": start_line,
            "end_line": end_line,
            "collection_id": collection_id,
            "metadata_json": json.dumps(metadata or {}),
        })
        try:
            self._chroma_collection(collection_id).upsert(
                ids=[chunk_id],
                embeddings=[embedding],
                documents=[content],
                metadatas=[stored_metadata],
            )
        except InvalidDimensionException as error:
            raise ValueError(str(error)) from error
        return chunk_id

    @staticmethod
    def _result(
        chunk_id: str,
        document: str,
        metadata: dict,
        score: float,
    ) -> dict:
        user_metadata = json.loads(metadata.pop("metadata_json", "{}"))
        return {
            "id": chunk_id,
            "collection_id": metadata["collection_id"],
            "source_file": metadata["source_file"],
            "start_line": metadata["start_line"],
            "end_line": metadata["end_line"],
            "content": document,
            "metadata": user_metadata,
            "score": score,
        }

    def search(
        self,
        query_vector: list[float],
        top_k: int = 5,
        filters: dict | None = None,
        collection_id: str = "default",
        threshold: float | None = None,
    ) -> list[dict]:
        """Return Chroma cosine-similarity results, optionally thresholded."""
        collection_config = self.get_collection(collection_id)
        dimension = collection_config["embedding_dimension"]
        if dimension is not None and len(query_vector) != dimension:
            raise ValueError("Query vector dimension does not match collection dimension")
        if threshold is not None and not 0.0 <= threshold <= 1.0:
            raise ValueError("threshold must be between 0 and 1")
        chroma_collection = self._chroma_collection(collection_id)
        if chroma_collection.count() == 0:
            return []

        result = chroma_collection.query(
            query_embeddings=[query_vector],
            n_results=chroma_collection.count(),
            include=["documents", "metadatas", "distances"],
        )
        matches = []
        for chunk_id, document, metadata, distance in zip(
            result["ids"][0], result["documents"][0],
            result["metadatas"][0], result["distances"][0],
        ):
            if filters and any(
                json.loads(metadata.get("metadata_json", "{}")).get(key) != value
                for key, value in filters.items()
            ):
                continue
            score = 1.0 - distance
            if threshold is not None and score < threshold:
                continue
            matches.append(self._result(chunk_id, document, dict(metadata), score))
        return matches[:top_k]

    def get_all(self) -> list[dict]:
        """Return every chunk in the default collection."""
        return self.get_all_for_collection("default")

    def get_all_for_collection(self, collection_id: str = "default") -> list[dict]:
        """Return every chunk and embedding in one collection."""
        collection = self._chroma_collection(collection_id)
        if collection.count() == 0:
            return []
        result = collection.get(include=["documents", "metadatas", "embeddings"])
        chunks = []
        for chunk_id, document, metadata, embedding in zip(
            result["ids"], result["documents"], result["metadatas"], result["embeddings"]
        ):
            item = self._result(chunk_id, document, dict(metadata), 0.0)
            item["vector"] = list(embedding)
            chunks.append(item)
        return chunks

    def close(self):
        """Close the configuration database."""
        self.conn.close()
        if self._tempdir is not None:
            self._tempdir.cleanup()
