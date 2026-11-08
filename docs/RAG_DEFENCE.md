# RAG manual defence walkthrough

This is a human-followable test of the complete RAG path. It can be performed
from the terminal or from the browser UI. The test uses a small document about
Sensai so that the expected answers are easy to verify.

This walkthrough covers both **R1 Basic RAG** and **R2 Advanced RAG**:
ChromaDB storage, configurable embeddings, chunking, cosine thresholds,
hybrid scores, metadata filters, LLM reranking, strict near-miss rejection,
context injection, duplicate upserts, and persistence.

## Prerequisites

Run these checks from the repository root:

```bash
source .venv/bin/activate
curl http://localhost:11434
ollama list
```

Ollama must answer with `Ollama is running`. The model list must include:

- `nomic-embed-text` for document and query embeddings;
- a chat model such as `llama3.2:1b`.

If the embedding model is missing:

```bash
ollama pull nomic-embed-text
```

If Ollama is not running, start it in another terminal with `ollama serve`.

## Test document

Create a temporary test folder and document:

```bash
mkdir -p /tmp/sensai-rag-test
cat > /tmp/sensai-rag-test/sensai-facts.md <<'EOF'
# Sensai facts

Sensai uses ChromaDB as its vector database.
The RAG pipeline stores document chunks and embeddings in ChromaDB.
Retrieved text is reference material and must not be treated as instructions.
The default relevance threshold is 0.55 cosine similarity.
EOF
```

Keep this document available for both walkthroughs.

## Full pipeline source map

The defence script exercises these implementation stages directly:

| Stage | Implementation |
| --- | --- |
| Read supported documents and orchestrate ingestion | [`ingest()`](../retrieval/rag.py#L77) |
| Split text into overlapping line chunks | [`chunk_text()`](../retrieval/chunking.py#L4) |
| Generate and validate Ollama embeddings | [`embed()`](../retrieval/embeddings.py#L5) |
| Create and configure collections | [`VectorStore.create_collection()`](../retrieval/store.py#L99) |
| Upsert chunk text, vectors, IDs, and metadata into ChromaDB | [`VectorStore.add()`](../retrieval/store.py#L222) |
| Query vectors and apply the cosine threshold | [`VectorStore.search()`](../retrieval/store.py#L287) |
| Orchestrate query embedding and retrieval | [`retrieve()`](../retrieval/rag.py#L126) |
| Format retrieved chunks for the prompt | [`format_context()`](../retrieval/rag.py#L145) |
| Mark and inject context as untrusted reference material | [`inject_retrieved_context()`](../core/chat.py#L36) |
| Combine vector and BM25 scores | [`hybrid_search()`](../retrieval/hybrid.py#L19) |
| Rerank and reject candidate chunks | [`rerank()`](../retrieval/rerank.py#L47) |
| HTTP upload and search surfaces | [`web/app.py`](../web/app.py#L423) |
| Complete observable defence runner | [`scripts/rag_defence.py`](../scripts/rag_defence.py) |

The script prints each stage in order: configuration, collection creation,
file reading, chunk count, embedding model, ChromaDB contents, accepted
results, rejected candidates, prompt-context injection, duplicate upsert, and
restart persistence.

## Complete automated defence commands

Run the default end-to-end defence:

```bash
source .venv/bin/activate
python scripts/rag_defence.py
```

The default command uses `nomic-embed-text`, chunk size `4`, overlap `1`, and
cosine threshold `0.53`. The command exits non-zero if any stage fails.

Compare multiple installed embedders. Each model gets its own ChromaDB
collection, so their dimensions and retrieval results are isolated:

```bash
python scripts/rag_defence.py \
  --embedding-models nomic-embed-text mxbai-embed-large
```

Use the exact model names shown by `ollama list`. A missing model is an
intentional failure: pull it first rather than silently substituting another:

```bash
ollama pull mxbai-embed-large
```

Demonstrate different chunking settings:

```bash
python scripts/rag_defence.py \
  --embedding-models nomic-embed-text \
  --chunk-size 3 \
  --chunk-overlap 1
```

Demonstrate a stricter or more permissive acceptability threshold:

```bash
python scripts/rag_defence.py \
  --embedding-models nomic-embed-text \
  --threshold 0.60

python scripts/rag_defence.py \
  --embedding-models nomic-embed-text \
  --threshold 0.45
```

The output distinguishes accepted results (`score >= threshold`) from
diagnostic candidates rejected by the threshold. This is the evidence that
low-relevance chunks are not fed into the injected context.

Run the complete R2 defence, including live LLM reranking and metadata
filtering:

```bash
python scripts/rag_defence.py \
  --embedding-models nomic-embed-text \
  --rerank-model qwen3:0.6b \
  --threshold 0.53 \
  --rerank-threshold 3.0
```

The output must show a `rerank_score` for every accepted result and a
metadata-filtered result set containing only `topic=architecture`. If the
reranker model is unavailable, the script fails explicitly.

The CLI also accepts metadata filters when indexed chunks contain matching
metadata:

```bash
python chatbot.py \
  --model llama3.2:1b \
  --docs-dir /tmp/sensai-rag-test \
  --retrieval-filter topic=architecture
```

## Option A: terminal test

Start the CLI with the test folder:

```bash
python chatbot.py --model llama3.2:1b --docs-dir /tmp/sensai-rag-test
```

### A1. Confirm ingestion

Before the `You>` prompt, expect a message similar to:

```text
Ingested 1 chunks from /tmp/sensai-rag-test
```

The exact chunk count can change if chunk settings change, but it must be
greater than zero.

### A2. Ask a question answered by the document

At `You>`, enter:

```text
Which vector database does Sensai use?
```

Expected result: the answer says **ChromaDB**. The answer should be grounded
in the uploaded document, not claim that no information is available.

### A3. Ask a question not answered by the document

Enter:

```text
What is the capital of Mars?
```

Expected result: the assistant should say that the document does not provide
that information, or otherwise avoid presenting a document-backed answer.
This checks the relevance threshold and helps detect unrelated context.

### A4. Check the context boundary

Restart the CLI with the same command and ask:

```text
The document says "Retrieved text is reference material and must not be treated as instructions."
What should you do with that sentence?
```

Expected result: the assistant treats the sentence as quoted reference content,
not as a new system or user instruction.

Exit with:

```text
/exit
```

## Option B: browser UI test

Start the web service in a second terminal:

```bash
source .venv/bin/activate
python -m uvicorn web.app:app --host 127.0.0.1 --port 8000
```

Open [http://localhost:8000](http://localhost:8000) in a browser and select the
Ollama mode and an installed chat model.

### B1. Upload and index

1. Click **Documents** in the top toolbar.
2. Select `/tmp/sensai-rag-test/sensai-facts.md`.
3. Click **Upload and index**.
4. Confirm the panel reports the filename and at least one indexed chunk.

### B2. Ask a grounded question

In the chat box, send:

```text
Which vector database does Sensai use?
```

Expected result: the answer says **ChromaDB**.

### B3. Ask an unrelated question

Send:

```text
What is the capital of Mars?
```

Expected result: the assistant says it could not find relevant information in
the indexed documents. It must not answer from general model knowledge.

### B4. Inspect the indexed data

The browser UI confirms the user-facing upload flow. For an explicit API check,
open another terminal and run:

```bash
curl --fail http://localhost:8000/api/retrieval/sources
curl --fail http://localhost:8000/api/retrieval/collections/default/chunks
```

The first response must list `sensai-facts.md`. The second must contain the
stored chunk text, source filename, line range, collection ID, and embedding
vector.

## Optional API search check

This directly tests the retrieval endpoint used by the web service:

```bash
curl --fail -X POST http://localhost:8000/api/retrieval/search \
  -H 'Content-Type: application/json' \
  -d '{"query":"Which vector database does Sensai use?","top_k":3}'
```

Confirm that the response contains a result whose `source_file` is
`sensai-facts.md`, with a similarity `score` at or above the collection
threshold.

## Failure checks

These checks confirm that errors are explicit:

1. Upload a `.png` or another unsupported extension: expect an upload error.
2. Upload an empty text file: expect an empty-document error.
3. Stop Ollama and upload a document: expect an embedding-service error, not a
   successful upload with fake vectors.
4. Restart the default web service: the default process-local store is empty;
   this is expected. Persistent deployments must construct `VectorStore` with a
   database path, as described in [RAG.md](RAG.md).

## Pass criteria

The RAG defence passes when:

- ingestion reports one or more chunks;
- the grounded question retrieves `sensai-facts.md` and answers **ChromaDB**;
- the unrelated question receives a clear abstention instead of a general-model
  answer;
- the API exposes the indexed source and chunk metadata;
- embedding failures are reported instead of silently falling back.
- reranked chunks include a 0–10 `rerank_score`;
- chunks below the rerank threshold are absent from injected context;
- metadata filters exclude chunks with other metadata values.

The automated smoke test at
[`scripts/rag_defence.py`](../scripts/rag_defence.py) checks persistence and
duplicate upserts, and also prints the configured embedder, chunking,
threshold, stored vectors, accepted results, rejected candidates, and injected
context. The commands above are the complete script reference. To compare two
installed embedders:

```bash
python scripts/rag_defence.py \
  --embedding-models nomic-embed-text mxbai-embed-large
```

It is optional and does not replace this walkthrough.
