# Semantic-cache defence walkthrough

This is a reproducible manual and automated check of Sensai's semantic cache.
Its purpose is safety first: only a fresh, standalone, unguarded Ollama chat
turn may use the cache. Turns influenced by external or prior context must
always reach the model.

## What this proves

The walkthrough verifies:

- a semantically similar standalone question can be served from cache;
- RAG, attachments, web-search evidence, MCP observations, conversation
  history, guarded requests, and structured output bypass it;
- cache lookup and write failures fall back to model generation;
- entries persist, can expire by TTL, and can be inspected or cleared;
- document-backed turns cannot reuse an answer after indexed documents change.

It intentionally does **not** test RAG-aware cache keys. RAG caching is disabled
until a document-context fingerprint design is implemented.

## Prerequisites

Run commands from the repository root with the virtual environment active:

```bash
source .venv/bin/activate
ollama serve
```

In another terminal, install a chat and embedding model if necessary:

```bash
ollama pull llama3.2:1b
ollama pull nomic-embed-text
```

Use an isolated database during this walkthrough so existing cache entries do
not affect the result:

```bash
export SENSAI_SEMANTIC_CACHE_DB=/tmp/sensai-semantic-cache-defence.sqlite3
export SENSAI_SEMANTIC_CACHE_ENABLED=true
export SENSAI_SEMANTIC_CACHE_THRESHOLD=0.92
export SENSAI_SEMANTIC_CACHE_EMBEDDING_MODEL=nomic-embed-text
rm -f /tmp/sensai-semantic-cache-defence.sqlite3
```

`SENSAI_SEMANTIC_CACHE_MAX_AGE_SECONDS` is optional. Leave it unset for the
hit and bypass tests; set it later for the expiration test.

The cache singleton is rebuilt whenever any cache-affecting runtime setting
changes: enabled state, database path, similarity threshold, embedding model,
Ollama host, or TTL. This prevents a long-lived process from continuing to use
stale cache configuration. The regression test
`test_cache_singleton_rebuilds_for_every_runtime_configuration_change` verifies
each setting. In normal CLI or web operation, set environment variables before
starting the process.

## Automated regression tests

Run the focused tests first:

```bash
python3 -m unittest tests.test_semantic_cache -v
```

Expected: all tests pass. They cover cache hits and misses, isolation by cache
key, restart persistence, TTL cleanup, malformed/wrong-dimension embeddings,
context-dependent bypasses, disabled caching, and lookup/write fallback.

If test collection reports `ModuleNotFoundError: chromadb`, install project
requirements before retrying:

```bash
python3 -m pip install -r requirements.txt
```

## Terminal walkthrough

Start the CLI:

```bash
python3 chatbot.py --model llama3.2:1b
```

### A1. Inspect an empty cache

At `You>`, run:

```text
/cache stats
```

Expected: `0 entries` when using the isolated database.

### A2. Store a standalone answer and verify a hit

Ask a stable general question:

```text
What is the capital of France?
```

The first request is a cache miss and calls the model. Run:

```text
/cache stats
```

Expected: at least one entry. Next, use `/clear` before asking the same question
a second time:

```text
/clear
What is the capital of France?
```

`/clear` starts a new session, removing conversation history while retaining the
cache. The second answer should return without a second model generation. In
server logs, expect `[A6 CACHE HIT]`. `/cache stats` should show at least one
entry.

To make this visible in a terminal, configure Python logging before launch:

```bash
PYTHONLOGLEVEL=INFO python3 chatbot.py --model llama3.2:1b
```

Depending on the logging configuration, cache entries appear as `[A6 CACHE
MISS]`, `[A6 CACHE HIT]`, `[A6 CACHE BYPASS]`, `[A6 CACHE ERROR]`, and `[A6
CACHE EVICT]`.

### A3. Verify history bypass

Without using `/clear`, ask a follow-up:

```text
Repeat that answer in exactly three words.
```

Expected: the model is called and logs `[A6 CACHE BYPASS]`; no additional cache
entry is written for this follow-up. It depends on the preceding answer.

### A4. Verify RAG and document-change bypass

Exit, then make a small document:

```bash
mkdir -p /tmp/sensai-cache-docs
printf 'The project colour is blue.\n' > /tmp/sensai-cache-docs/facts.md
python3 chatbot.py --model llama3.2:1b --docs-dir /tmp/sensai-cache-docs
```

Ask:

```text
What is the project colour?
```

Expected: an answer based on **blue**, with `[A6 CACHE BYPASS]`. Exit, replace
`blue` with `green`, restart with the same command, and ask again. Expected:
the answer is based on **green** and does not reuse the old answer. RAG-enabled
turns are never read from or written to the semantic cache.

### A5. Verify file, guarded, structured, search, and MCP bypass

Each command below must call the model rather than use or add a semantic cache
entry. Check `/cache stats` before and after each isolated test; the count must
not increase for that turn.

```bash
# File attachment: run /read <file> and ask a question about it.
python3 chatbot.py --model llama3.2:1b --workspace examples

# Guarded turn: use a project guardrail policy.
python3 chatbot.py --model llama3.2:1b --guardrails PATH_TO_POLICY.json

# Structured turn.
python3 chatbot.py --model llama3.2:1b --json-schema schemas/tasks.json

# Web search (requires a configured SearXNG instance).
python3 chatbot.py --model llama3.2:1b --search-url http://127.0.0.1:8080

# MCP (requires a trusted local MCP config).
python3 chatbot.py --model llama3.2:1b --mcp-config configs/mcp.demo.json
```

For the last two, issue `/search <query>` and `/mcp <request>` respectively.
For the file test, `/read` queues the file; the next ordinary question is the
context-dependent turn. For structured mode, submit a schema-valid request.

### A6. Verify expiration and maintenance

Start a fresh CLI with a one-second TTL:

```bash
SENSAI_SEMANTIC_CACHE_MAX_AGE_SECONDS=1 \
python3 chatbot.py --model llama3.2:1b
```

Ask a standalone question, use `/clear`, wait at least two seconds, and ask it
again. Expected: a cache miss, model generation, and eventually an `[A6 CACHE
EVICT]` log. Confirm expiry with `/cache stats`.

Run `/cache clear` and then `/cache stats`. Expected: `0 entries`.

## Browser UI walkthrough

Start the service with the same environment variables:

```bash
python3 -m uvicorn web.app:app --host 127.0.0.1 --port 8000
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000), choose **Ollama**, and set
an installed model.

1. Send a standalone question such as `What is the capital of France?`.
2. Click **Clear** to remove the conversation while retaining the cache.
3. Send the same question again. Server logs should show an A6 cache hit.
4. Choose **Web search** and ask the same question. Expect an A6 bypass because
   retrieved search evidence is appended before generation.
5. Upload/index a document using **Documents**, ask a document question, change
   the document, re-index it, and ask again. Both are cache bypasses.
6. Use the UI Clear button, then send a follow-up before clearing in a separate
   run. Follow-ups bypass cache because saved conversation history is included.

The browser has no cache-management control. Use the CLI `/cache stats` and
`/cache clear` against the same `SENSAI_SEMANTIC_CACHE_DB` path for inspection
and manual invalidation.

## Failure checks

1. Stop Ollama's embedding service after a cache entry exists, then ask an
   eligible standalone question. Expected: cache lookup failure is logged and
   normal model generation is attempted; the cache error must not crash chat.
2. Make the cache database unwritable or temporarily lock it. Expected: normal
   model generation still completes when a cache read/write fails.
3. Change `SENSAI_SEMANTIC_CACHE_EMBEDDING_MODEL` to a model with a different
   vector dimension. Expected: incompatible stored vectors are cache misses,
   not false hits or crashes.
4. Restart the process and repeat the standalone hit test. Expected: the cache
   entry persists when the same cache database path is used.

## Pass criteria

The semantic-cache defence passes when:

- a fresh standalone repeat produces a cache hit;
- history and every external/context-dependent turn bypass cache;
- changing an indexed document produces a fresh RAG answer;
- expired entries are removed and `/cache clear` empties the cache;
- cache lookup or storage errors never prevent model generation;
- the cache remains partitioned by model, Ollama host, and system prompt.
