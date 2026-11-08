# Retrieval-augmented generation (RAG)

Sensai's RAG pipeline indexes local text documents, retrieves relevant chunks,
and injects those chunks into the same prompt used by the CLI and web chat.
ChromaDB is the vector database; no in-memory or manual-cosine replacement is
used by the production implementation.

## R1 and R2 status

R1 Basic RAG and R2 Advanced RAG are implemented. R2 adds hybrid
vector/BM25 ranking, metadata filters, LLM reranking, per-chunk score
breakdowns, and strict rejection before context injection. Both CLI and web
chat use the advanced retrieval path.

## Architecture

The pipeline is split into five steps:

1. **Ingestion** accepts `.txt` and `.md` files, decodes UTF-8, and ignores
   unsupported files.
2. **Chunking** splits text into line-based chunks using the collection's
   `chunk_size` and `chunk_overlap`.
3. **Embedding** calls Ollama's `/api/embed` endpoint. The default model is
   `nomic-embed-text`. Invalid, missing, or unavailable embeddings are reported
   as errors; there is no silent fallback.
4. **Storage** upserts each chunk into a ChromaDB collection. Chroma stores the
   chunk ID, document text, embedding, and metadata.
5. **Retrieval** embeds the query, searches the selected collection using
   cosine distance, converts distance to similarity (`score = 1 - distance`),
   and keeps results whose score is greater than or equal to the threshold.
6. **Hybrid ranking** combines normalized cosine and BM25 scores after applying
   metadata filters.
7. **Reranking** sends candidates to the configured Ollama judge. Each chunk
   receives a 0–10 `rerank_score`; chunks below the rerank threshold are
   removed before context injection. Strict retrieval reports judge failures
   instead of passing unverified chunks.

The CLI and web service both use `retrieve_advanced()` and the shared context
injection helper. Retrieved material is marked as untrusted reference data in
the prompt so document text is not treated as an instruction.

## Persistence and collections

`VectorStore` uses two local stores:

| Store | Persistent location when `db_path` is `path/to/rag.db` | Contents |
| --- | --- | --- |
| ChromaDB | `path/to/rag.db.chroma/` | Vectors, documents, chunk metadata |
| SQLite sidecar | `path/to/rag.db.config.sqlite3` | Collection configuration |

The SQLite sidecar is configuration storage only; it is not a vector database.
The default collection is `default`. Each collection records:

- collection ID and display name;
- embedding model and observed embedding dimension;
- chunk size and overlap;
- similarity metric (`cosine`);
- relevance threshold in the range `0.0` to `1.0` (default `0.55`);
- creation and update timestamps.

Chunk IDs are deterministic hashes of source filename, line range, and content.
Re-ingesting the same chunk therefore performs a ChromaDB `upsert` instead of
creating a duplicate.

## Configuration

The shared settings can be overridden with environment variables:

| Variable | Default | Meaning |
| --- | --- | --- |
| `SENSAI_RETRIEVAL_COLLECTION` | `default` | Collection used by chat |
| `SENSAI_EMBEDDING_MODEL` | `nomic-embed-text` | Ollama embedding model |
| `SENSAI_CHUNK_SIZE` | `20` | Lines per chunk |
| `SENSAI_CHUNK_OVERLAP` | `5` | Overlapping lines |
| `SENSAI_RETRIEVAL_TOP_K` | `5` | Maximum chat results |
| `SENSAI_RERANK_MODEL` | `qwen3:0.6b` | Ollama reranking model |
| `SENSAI_RERANK_THRESHOLD` | `3.0` | Minimum rerank score from 0 to 10 |
| `SENSAI_RERANK_TOP_N` | `10` | Maximum candidates sent to reranking |
| `SENSAI_REWRITE_MODEL` | `qwen3:0.6b` | Ollama query-rewrite model |

Collection configuration is the source of truth when ingestion or retrieval is
given a collection ID. Collection-specific settings can also be managed through
the collection API.

## HTTP API

The web service exposes:

| Method | Endpoint | Purpose |
| --- | --- | --- |
| `POST` | `/api/retrieval/upload` | Upload and index one UTF-8 `.txt` or `.md` file (5 MiB maximum) |
| `GET` | `/api/retrieval/sources` | List indexed sources and chunk counts |
| `POST` | `/api/retrieval/collections` | Create a collection |
| `GET` | `/api/retrieval/collections` | List collections |
| `GET` | `/api/retrieval/collections/{id}` | Inspect collection configuration |
| `PATCH` | `/api/retrieval/collections/{id}` | Update collection configuration |
| `DELETE` | `/api/retrieval/collections/{id}` | Delete a non-default collection |
| `GET` | `/api/retrieval/collections/{id}/chunks` | Inspect stored chunks, vectors, and metadata |
| `POST` | `/api/retrieval/search` | Semantic search with `query`, `top_k`, `threshold`, and metadata filters |

Upload failures are explicit: unsupported extensions return `415`, oversized
documents `413`, invalid or empty documents `400`/`422`, and embedding service
failures `502`.

## Manual verification

Follow the [RAG manual defence walkthrough](RAG_DEFENCE.md) for a terminal or
browser test with expected questions and results.

For an automated smoke check, with Ollama running and `nomic-embed-text`
installed, run:

```bash
python scripts/rag_defence.py
```

The script creates a temporary persistent store, indexes two documents, checks
relevant and unrelated queries, prints accepted and rejected scores, verifies
context injection, duplicate upserts, closes and reopens the store, and removes
its temporary files. Use `--embedding-models model-a model-b` to compare
installed embedders, and use `--chunk-size`, `--chunk-overlap`, and `--threshold`
to demonstrate collection configuration.

Focused automated coverage is in `tests/test_store.py`, `tests/test_rag.py`,
`tests/test_document_upload.py`, and `tests/test_retrieval_api.py`.

## Limitations

- Ollama must be running locally (or at the configured host) for embedding.
- The default web `VectorStore(':memory:')` is process-local; use a persistent
  `VectorStore` path for service deployments that must survive restarts.
- Updating a collection's embedding model does not re-embed existing chunks;
  re-ingest the documents into a fresh or updated collection when changing
  embedding dimensions or models.
- Similarity thresholds are collection-level and use cosine similarity. LLM
  reranking uses its own configurable 0–10 rejection scale. Both stages run
  before context injection in CLI and web chat.
- Strict reranking requires the configured Ollama judge model. An unavailable
  judge is reported as a retrieval failure rather than a silent fallback.
