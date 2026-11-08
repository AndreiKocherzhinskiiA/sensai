# Sensai

**A local AI assistant for learning, researching and testing software ideas.**

Sensai connects an Ollama model to project documents, external tools and a
restricted Python runtime. Use the terminal or browser to ask follow-up questions,
switch between tutor and reviewer personas, turn notes into a task list, and check
an answer against documents or live web sources.

[Get started](START.md) · [Documentation](docs/README.md) · [Feature status](docs/FEATURES.md) · [User stories](docs/USER_STORIES.md)

## What you can do

| Workflow | Where to start |
| --- | --- |
| CLI and selectable model | argparse, required `--model` |
| Progressive responses | `stream=True`, read NDJSON chunks, print with flush |
| In-session history | resend system prompt + successful previous turns + new user turn |
| Prompt loaded from a file | `--system-prompt`, UTF-8 file validation |
| Missing model | HTTP 404 with model pull instruction |
| Empty input | explanation, no request sent |
| Connection failure | explicit error, return to input |

Also handles timeouts, HTTP errors, errors within streams, malformed responses,
empty answers and streams that end without a completion marker.
Only a complete question/answer pair is committed to history. Partial visible output
is not reused after an error. All model calls use `POST /api/chat` directly.

## Structured JSON output (T5, member B)

Install the updated requirements, then run from the project directory:

```bash
python3 chatbot.py --model llama3.2:1b --json-schema schemas/plan.json
python3 chatbot.py --model llama3.2:1b --json-schema schemas/tasks.json --json-attempts 2
```

For the first example, ask `Make a three-step plan to learn Python functions`.
For the second, ask `Extract tasks: fix login (high priority), update README (low priority)`.
The model must return the structure declared in the selected file. The schema is
sent through Ollama's `format` field and checked locally with `jsonschema`.

JSON responses are buffered until complete and valid; ordinary chat still streams.
Invalid content gets at most two total attempts by default (configurable from 1 to 5).
Rejected output and retry instructions never enter conversation history. Transport
errors and cancellation are not retried. After exhaustion, the chat remains usable.

This increment accepts draft 2020-12 inline schemas with a top-level object and no
`$ref`/`$dynamicRef` references. `format` annotations (such as email) are not asserted.
Validation verifies structure, not factual accuracy or permission to execute an action.
Both CLI examples passed on the user's local model (see docs/VERIFY.md).
Open `/structured` in the web service to extract tasks, create plans or define
custom fields, then complete checklist items and download validated JSON.
CLI equivalents start with `/extract tasks <text>` and `/extract plan <goal>`.
Results persist across restarts. Structured generation requires a real Ollama model
and does not fall back to stub output. See [the workflow and verification guide](docs/STRUCTURED_WORKSPACE.md).
The new workflow's live acceptance walkthrough remains to be run in WSL.

## Files and architecture

- `chatbot.py`: CLI, prompt loading, session loop and direct HTTP streaming
- `prompts/system.txt`: editable assistant instructions
- `requirements.txt`: HTTP, web server and JSON Schema dependencies
- `structured/output.py`: reusable JSON validation and bounded correction loop
- `schemas/`: example plan and task-extraction schemas
- `tests/test_structured.py`: validation and retry boundary checks
- `file_access/reader.py`: bounded reads beneath an explicit workspace root
- `file_access/writer.py`: immutable save previews and approved create-only writes
- `docs/FILE_ACCESS.md`: T4 behavior, permission boundaries and manual checks
- `tests/test_chatbot.py`: local HTTP contract and error tests
- `docs/SCOPING.md`: full 28-feature scope, architecture, ownership and six-week plan for four members
- `docs/USER_STORIES.md`: stories and observable acceptance criteria
- `docs/VERIFY.md`: automated results and manual demo checklist
- `docs/RAG.md`: ChromaDB architecture, configuration, APIs and RAG defence
- `docs/RAG_DEFENCE.md`: step-by-step terminal and browser RAG test
- `docs/SEMANTIC_CACHE_DEFENCE.md`: semantic-cache safety, TTL, and bypass walkthrough
- `scripts/rag_defence.py`: live Ollama/ChromaDB end-to-end demonstration
- `START.md`: installation from scratch, first launch, repeat launch and troubleshooting

`main()` loads configuration and the prompt. `run_chat()` owns history and terminal IO.
`stream_reply()` sends the message list and yields response text without managing history.
File access supports explicitly requested context and confirmed creation in the CLI.
There is no persistence, full RAG pipeline, existing-file editing or code execution yet.
| Chat with a local model, resume sessions and switch personas | [First launch](START.md) |
| Ask questions about uploaded documents | [Document retrieval](docs/WEB_DOCUMENT_UPLOAD.md) |
| Search the web and inspect the returned sources | [Web search](docs/WEB_SEARCH.md) |
| Call project and time tools with visible results | [MCP tools](docs/MCP.md) |
| Execute Python, inspect errors and verify a correction | [Sandbox](docs/SANDBOX.md) |
| Read permitted files and approve creation of new files | [File access](docs/FILE_ACCESS.md) |
| Extract tasks or plans, track progress and export validated JSON | [Structured workspace](docs/STRUCTURED_WORKSPACE.md) |
| Inspect runs, compare prompt versions and evaluate answers | [Quality and evaluation](docs/QUALITY.md) |

The [feature register](docs/FEATURES.md) distinguishes available workflows,
partial integrations and planned work. The full catalogue is a project target,
not a claim that every feature is complete.

## Start locally

Use **Python 3.10+ and Ollama**. The supported walkthrough is **Ubuntu 24.04**, directly
or in **WSL 2**; file permissions and the sandbox require Linux. No model API key
is needed. Follow [START.md](START.md) if you have not installed these tools.

With Ollama running, open an Ubuntu terminal in the project folder:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
ollama pull qwen3:0.6b
python chatbot.py --model qwen3:0.6b
```

At `You>`, ask `Explain Python functions with one small example.` Then ask a
follow-up. Enter `/exit` to return to the terminal. The small demonstration model
is convenient for setup; its answers still need checking.

For the browser, run this in the project folder with the environment active:

```bash
export SENSAI_MODEL=qwen3:0.6b
python -m uvicorn web.app:app --host 127.0.0.1 --port 8000
```

Open [chat](http://127.0.0.1:8000), [structured workspace](http://127.0.0.1:8000/structured)
or [dashboard](http://127.0.0.1:8000/dashboard). Select **Ollama** for real chat.
Optional search, MCP and sandbox setup is covered in the linked guides above.

## How it is built

Python handles the CLI and shared chat services; FastAPI serves the browser and
API; SQLite stores sessions, results and run events. Model requests go directly
to Ollama over HTTP. MCP uses its transport SDK, and the Python sandbox uses
Bubblewrap and seccomp. There is no LLM framework.

See [architecture](docs/ARCHITECTURE.md) for request flow and the code map, and
[configuration](docs/CONFIGURATION.md) for options, storage and feature modes.

All settings are centralized in `core/config.py` and can be overridden via environment variables or CLI flags:

| Variable | Description | Default |
| --- | --- | --- |
| `SENSAI_OLLAMA_HOST` | Fixed server-side Ollama HTTP URL (client cannot choose outbound host) | `http://localhost:11434` |
| `SENSAI_MODEL` | Default model name for web runs | `llama3.2:1b` |
| `SENSAI_MODE` | Runtime mode: `ollama` (real model) or `stub` (offline simulation) | `ollama` (auto-falls back if unreachable) |
| `SENSAI_DB` | Path to SQLite session database | `sensai_sessions.db` |
| `SENSAI_EVENTS_DB` | Path to SQLite observability event store | `sensai_events.db` |
| `SENSAI_PROMPTS_DB` | Path to SQLite prompt versioning store | `sensai_prompts.db` |
| `SENSAI_ARTIFACTS_DB` | Path to SQLite artifact store | `sensai_artifacts.db` |
| `SENSAI_SNAPSHOTS_DB` | Path to SQLite public snapshot store | `sensai_snapshots.db` |
| `SENSAI_WORKSPACE` | Root directory for workspace file read and write | `./workspace` |
| `SENSAI_SEMANTIC_CACHE_ENABLED` | Enable caching for standalone, unguarded turns only | `true` |
| `SENSAI_SEMANTIC_CACHE_DB` | SQLite cache database path | `retrieval/semantic_cache.sqlite3` |
| `SENSAI_SEMANTIC_CACHE_THRESHOLD` | Minimum cosine similarity for a cache hit (0–1) | `0.92` |
| `SENSAI_SEMANTIC_CACHE_EMBEDDING_MODEL` | Ollama embedding model used by the cache | `nomic-embed-text` |
| `SENSAI_SEMANTIC_CACHE_MAX_AGE_SECONDS` | Optional TTL; unset keeps entries indefinitely | unset |

The cache is intentionally bypassed for RAG/retrieval, file attachments, web evidence,
MCP observations, conversation history, structured output, and guarded turns. In the
CLI, `/cache stats` reports active entries and `/cache clear` removes them.

### Running the Web Service
## Verify and demonstrate

```bash
python -m pip install pytest
python -m pytest tests -q
```

Some checks require the optional MCP and sandbox dependencies. See
[verification](docs/VERIFY.md) for prerequisites, recorded evidence and the
difference between automated tests and real-model demonstrations.

For the defense, use the [30-minute runbook](docs/DEMO.md) and
[persona question set](docs/PERSONA_CHECKS.md).

This is a local educational application. Model output and judge scores are not
proof of correctness; the UI and API have no authentication. Keep the documented
localhost binding. Current limitations are listed with each feature rather than
hidden behind a single “complete” label.
