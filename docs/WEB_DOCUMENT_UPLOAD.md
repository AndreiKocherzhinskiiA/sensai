# Web document upload

The SensAI chat page has a **Documents** button in the top toolbar. Open it to upload a UTF-8 `.txt` or `.md` file (maximum 5 MB). Select **Upload and index** to split the document into chunks, embed them with Ollama, and store them in the same ChromaDB-backed vector store used by RAG. The panel shows the indexed source names and chunk counts.

The upload endpoint is `POST /api/retrieval/upload` with a multipart form field named `file`. Unsupported extensions, empty files, oversized files, and non-UTF-8 content are rejected with a user-facing error. If the embedding service is unavailable, indexing fails explicitly with an embedding-service error; documents are never silently assigned fallback vectors.

After upload, ask a question whose answer appears in the document. The default web service uses a process-local store, so indexed documents are not retained after a server restart. Deployments that require restart persistence should construct `VectorStore` with a persistent database path; see [the RAG guide](RAG.md).
