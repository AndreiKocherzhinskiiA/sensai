# Member B: component integration contract

B's tools are integrated into ordinary web chat. The application first selects
whether an enabled tool is needed, then either runs the approved tool chain or
continues the existing streaming answer pipeline. Users do not switch to a
search or MCP mode. Other members' retrieval/session features remain in place.

## Entry points

| Stage | Entry point | Contract |
| --- | --- | --- |
| Content input/output policy | `Guardrails.check_input`, `messages`, `output`, `json_output` | Policy rejection raises `GuardrailError`; do not persist or release rejected content. |
| Tool discovery | `mcp_tools.gateway.connect` | Real MCP SDK initialization and discovery; stdio/Streamable HTTP discovery; tools follow the operator's all-discovered or explicit selection policy, with per-call approval by default. |
| Optional tool stage | `mcp_tools.runner.run_optional_turn`, `plan_workflow` | Validated ordered tool plan; an empty plan returns `None` to continue ordinary streaming chat. Arguments are generated per step using previous observations. |
| Action loop | `mcp_tools.runner.run_turn` | Validated proposals, exact-argument approval, real MCP observations, bounded calls. A text-only turn is not accepted as completed action. |
| Explicit Python task | `mcp_tools.runner.run_python_task` | Takes user-provided `code` and `goal`. First code is not rewritten by the model. Every execution needs the normal approval callback. Failed execution enters model repair. Label initial source as user-supplied in the UI. |
| Repair generation | `mcp_tools.runner.request_action` | A separate schema-constrained generation stage. Uses actual failed source/traceback. Repeated unchanged repair fails; malformed proposals never execute. |
| Workspace read/create/update | `WorkspaceReader`, `WorkspaceWriter`, `FilePolicy` | Descriptor-based access with separate read/create/write path grants, deny rules, byte limits and version-checked updates. |
| Structured workflow | `structured.workflow.generate_result` | Validates then saves; preserve cancellation, completion and export contracts. |

`run_optional_turn`, `run_turn` and `run_python_task` accept `approve(name, arguments)` (sync or async),
`notify(kind, payload)` (synchronous event callback), and `policy`. Reuse existing
web approval IDs and SSE events; do not auto-approve in the production adapter.
Events are `call` and `result`; approval is requested through the callback.
On cancellation, propagate `CancelledError` and close the connection context.
Do not report rollback of already completed tool actions.

## One tool catalogue for search, files, time and Python

```bash
mkdir -p local-output
cp -n examples/project_notes.md local-output/project_notes.md
export SENSAI_MCP_CONFIG=configs/mcp.integrated.json
export SENSAI_GUARDRAILS=guardrails/demo.json
export SENSAI_MODEL=qwen3:4b
export SENSAI_MODE=ollama
python -m uvicorn web.app:app --host 127.0.0.1 --port 8000
```

Use **Chat** with **Allow tools** enabled (the launcher's default). Search, read,
create, time and Python are selected automatically from the configured catalogue.
No tool name or special mode is required in the user's request. The SearXNG service still needs `python deploy/search/local.py` in
another terminal. The read → search → create chain was also live-tested with qwen3:4b; catalogue
availability alone is not the evidence. Inspect the three actual tool results.

Example request: “Read project_notes.md, search for official documentation for
the project's database, then create research.md with the findings and URLs.”
Review every proposed call, including the complete new file content.

To disable a capability, remove its tool from `allow_tools` (and `auto_approve`)
or use Off in the web connection panel. An empty allowlist disables all tools; Disable turns off the whole server. `create_project_file` is registered only when the operator's
file policy enables creation. `write_project_file` is registered only when write paths are granted. Keep all file tools out of `auto_approve`.
The host owns human consent; a direct SDK call bypasses the UI, so do not expose
these stdio servers as unauthenticated remote services.

`configs/files.demo.json` contains extension, byte-limit and operation/path permissions. See [File permissions](FILE_PERMISSIONS.md) for exact policy semantics and demonstration commands.
Policies cannot enable traversal, symlinks, hardlinks or special files. Creation never overwrites; existing-file updates require a write grant, expected SHA-256 and separate approval.
The filesystem boundary is not delegated to the LLM or a suffix filter.
CLI's existing `--workspace` and `--allow-create` defaults remain supported.

## Protocol and isolation evidence

```bash
python -m mcp_tools.verify
python -m pytest tests/test_b_integration.py tests/test_mcp.py tests/test_mcp_web.py tests/test_sandbox.py tests/test_file_access.py tests/test_file_write.py tests/test_structured_workflow.py tests/test_guardrails_web.py -q
```

The verifier uses actual SDK connections to the custom server, external Time
server and Python server. Its JSON records the negotiated protocol version,
server identity, discovery and call metadata, plus real negative execution
results. It does not use an LLM or mock MCP servers. Default report:
`local-output/b-protocol-isolation.json` (keep runtime data out of Git).

Tests separately cover wall/CPU/output limits, direct syscall attempts,
confirmation replay/expiry, policy filtering and structured-output failures.
The search adapter integration test uses a controlled local HTTP provider but
a real MCP child process; it is not evidence of internet availability.

## Recovery and evidence boundaries

Native tool calls remain the first path. If the model returns prose without an
initial action, one constrained proposal is requested and validated normally.
Python failure enters a dedicated repair stage with schema-constrained code generation
with a bounded output budget. Its output is a proposal, never an observed result.
Each retry needs approval and consumes the same execution-call budget.
Invalid repair, timeout, rejection or exhausted calls ends the turn as failure.

The explicit Python entry point makes the initial failing program reproducible;
it does not guarantee model repair or semantic correctness. A zero exit code
does not prove the user's goal was met. Review actual stdout and the code.
Do not claim universal recovery based on deterministic tests or a single live run.

## Team integration still required

Ordinary web requests already use the optional tool stage, existing approval
IDs, cancellation and persisted observations. Other team work, including the
full context inspector and feature-specific RAG/session changes, can build on
these entry points. Preserve T5 and A3 regression tests when extending routing.

## Reproduce the explicit-code repair flow

Create a local sample; it intentionally contains an undefined variable:

```bash
mkdir -p local-output
cat > local-output/repair-example.py <<'PY'
values = [10, 20, 30]
print(sum(values) / len(value))
PY
python -m mcp_tools.execute --code-file local-output/repair-example.py --goal "Calculate the arithmetic mean of the values. Fix any exception and execute the corrected program." --guardrails guardrails/demo.json --model qwen3:4b
```

Inspect and approve each program separately. First execution should show a real
NameError. A successful model correction should print the mean 20.0; inspect its
actual result, not just the final prose. This is an explicit user-source entry
point, not proof that the model initially generated the failing code.

## Recorded checks on 4 November 2026

- Real protocol/isolation verifier: 11/11 checks passed. Negotiated MCP versions,
  external Time server identity, tool discovery and calls are in the report.
- Live HTTP/SSE web: six recovery cases passed (three cases repeated twice):
  undefined variable, a missing measurement (`N/A`), and a syntax error.
  Each had an actual failed execution followed by approved successful execution;
  expected stdout was checked. One missing-data run needed two repairs.
- Standalone live recovery evaluator: six out of six passed with qwen3:4b.
  This uses the real model and isolated Python server, not mocked proposals.
- Real browser: model defaults, original error, second approval and actual
  `20.0` stdout checked; no JavaScript errors observed.
- Real web chain: read project notes → search for SQLite documentation → create
  a new report. Three tool observations and the resulting file verified.
- Tasks and plan workflows: generation, completion, reopen and export checked
  through HTTP. Rejected execution performed no tool call. Blocked input was
  refused before generation.
- Automated regression suite also exercises traversal, symlink/hardlink races,
  overwrite denial, direct syscall restrictions, cancellation, approval expiry
  and replay, policy failures, and invalid structured output.

Earlier qwen3:0.6b and llama3.2:1b trials failed some repairs. Use the tested
qwen3:4b configuration for this workflow. Six examples are finite acceptance
evidence, not proof of universal correctness. A successful process exit alone
does not establish a correct calculation; the evaluator checks expected stdout.

## Start the tested web configuration

From the repository directory, inside Ubuntu/WSL:

```bash
source .venv/bin/activate
python -m pip install -r requirements-mcp.txt
sudo apt install bubblewrap libseccomp2
ollama pull qwen3:4b
```

These are setup commands. For ordinary launches use one terminal:

```bash
source .venv/bin/activate
python deploy/start_b.py
```

The launcher starts the local SearXNG provider on port 8088 before the web app.
It reuses an already healthy provider and refuses to take over a port occupied
by an unrelated service. Ctrl+C stops only services it started; a reused provider
is left running. If search has not been installed yet, run once with
`python deploy/start_b.py --install-search`. Provider installation remains in its
separate pinned environment. Search results still depend on upstream engines
and network availability.

Open http://127.0.0.1:8000. The launcher checks Ollama/model/dependencies,
selects qwen3:4b, loads the integrated catalogue and demo guardrails, and seeds
`local-output/project_notes.md` only if absent. The browser loads the server's
model/mode defaults; changing modes no longer silently changes the model.
Use `--port 8010` if another application already owns port 8000.

The launcher intentionally binds loopback. This is a local single-user setup,
not an authenticated public deployment. File creation requires approval.

## Exercise repair in the web interface

1. Open **Run supplied Python** below the conversation. Paste:

   ```python
   values = [10, 20, 30]
   print(sum(values) / len(value))
   ```

2. In the message field enter: `Calculate the arithmetic mean. Fix errors and run the corrected code.`
3. Send. Inspect the first approval: it must still contain `len(value)`.
4. Approve. Expand the tool result: actual stderr contains `NameError`.
5. Inspect and approve the proposed corrected program.
6. The successful result must have exit code 0 and stdout `20.0`.
   The final answer renders that observed stdout directly.

The initial code is explicitly user-supplied. The model generates the repair.
Closing the code panel restores ordinary chat submission; explicit source works
without manually switching mode. Each retry consumes the same four-call budget.
Reject ends the turn; no extra automatic execution follows rejection.

For ordinary tool calls, stay in Chat and ask `Use python__execute_python to print
sum(range(11)).` The model supplies the code. When Python is the last action,
the final output is also rendered from its observed result, not model prose.
Other MCP tools, including Time, use the common model answer stage: a concise
explanation grounded in actual observations. There is no Time-specific formatter
or timezone table. The original result remains available in tool-result events
and the web result card; model-generated prose is not itself execution evidence.

## Reproduce evidence

```bash
python -m mcp_tools.verify
python -m mcp_tools.evaluate --model qwen3:4b --repeats 2
python -m pytest -q
```

The evaluation command deliberately auto-approves only the isolated Python tool
in its fixed test configuration. It does not auto-approve project-file tools.
This exception is confined to the test harness; CLI and web execution ask.
It exits nonzero on a failed case and records each source, exception, repair and
actual output in `local-output/b-live-recovery.json`. The protocol report is
`local-output/b-protocol-isolation.json`. Both are ignored by Git.

## Adapter details for teammates

For the existing web API, `POST /api/runs` accepts an optional `python_code`
string alongside `message` (goal) and `model`. Providing it routes to the
explicit-source runner; code is capped at 16 KiB UTF-8 and cannot be silently
rewritten by privacy filtering. Do not combine it with structured-output or
file-write request fields. Consume the existing SSE stream and use
`POST /api/runs/{run_id}/mcp/decision` for each approval ID. IDs expire after
120 seconds and cannot be replayed. Never treat the model's claim as a result.

For a new pipeline adapter, reuse `run_turn` or `run_python_task`; don't copy
their approval/execution loop. Observe `call`/`result` events and preserve
actual exceptions and cancellation. For direct low-level stages:

- `gateway.connect`: discovery and negotiated protocol information.
- `gateway.prepare`: tool allowlist, discovered JSON schema and policy checks.
- Host approval callback: exact proposed arguments, one decision per call.
- `gateway.call`: real SDK execution and bounded observations.
- `request_action`: constrained next action or repair proposal; never executes.
- `Guardrails`: text input/output controls; separate from OS isolation.
- `FilePolicy`: operator capability settings; separate from human consent.

The content policy is `guardrails/demo.json`, loaded by `Guardrails.from_file`.
Its demonstrator phrase rules are not a general semantic safety classifier.
The workspace policy is `configs/files.demo.json`, loaded by
`FilePolicy.from_file`. Tool availability and automatic approval are controlled
by `configs/mcp.integrated.json`, loaded by `mcp_tools.config.load_config`.
Browser/model input cannot replace these operator configurations.


## Automatic tools in ordinary chat (T3)

In the web interface, choose **Chat** (the default) and leave **Allow tools**
checked. Ask: `Find the official Python sqlite3 documentation online and give
me the source links.` The application selects the search tool, presents its
query for approval, performs the real search, and displays a **Web sources**
card with clickable URLs and retrieval time. No MCP/tool name is needed.

An ordinary question such as `Explain a Python function in two sentences`
continues the normal streaming answer path without executing a tool. Clear
the conversation between demonstrations when you want a fresh search rather
than a follow-up grounded in previously retrieved observations.

Uncheck **Allow tools** to disable discovery, planning and execution for a turn.
The ordinary model can still answer from its existing knowledge, but it receives
an explicit instruction not to claim a search or execution. Enforcement of no
tool execution is in the application, independent of that instruction.

API: `POST /api/runs` with `mode: "ollama"` (or omitted with the default server
configuration) enables automatic tools when the operator configured MCP servers.
`tools_enabled: false` skips the tool stage entirely. Only the configured
allowlist is available; client input cannot add servers or capabilities.
Legacy `mcp`/`search` API modes remain for compatibility, but are no longer
choices in the main chat UI. Structured-generation requests keep their own
validated path. Explicit Python source still uses its exact-source entry point.

Pipeline: existing context assembly → schema-constrained ordered workflow
→ either ordinary streamed generation (empty workflow), or per-step argument
generation/validation → human approval → real MCP call → observation/repair
→ next planned step → checked answer → persistence. A failed non-Python step
stops the workflow; completed side effects are not undone. Python repair remains
bounded by the same total call budget. JSON text resembling a tool proposal is
never accepted as a completed action or executed directly.
The optional routing call adds model latency. Capability selection receives
only the current user request and the discovered tool catalogue. It must not
replay completed requests or treat retrieved document text as a new action.
Before accepting an empty plan, one bounded review checks the current request
against the same discovered catalogue. It can retain an empty plan for ordinary
chat, an explicit no-tools request, or an unavailable capability; it cannot add
an undiscovered tool or bypass approval. This adds a model call on the no-tools
path. The persisted `loop_step` event with `phase: plan` records the model,
available tool names, selected steps and decision (`execute_plan`,
`no_tools_selected`, or `no_connected_tools`). The web shows it as **MCP · plan**.
Argument generation still receives conversation history and actual observations
to resolve references such as "that file" or "that time". Persona instructions
remain in the answer stage and cannot define which capabilities the server makes
available. A standalone web search must not introduce a file read or report write.

Routing regression coverage includes an ordinary search after completed tool
workflows, raw user input separated from injected document text, and references
resolved from prior observations. Reproduce the browser check in an existing
chat: send `Search the web for the official Python sqlite3 documentation.`.
There must be one search approval and actual web sources, with no local file
operation. This scenario was checked live on qwen3:4b, qwen3:0.6b and llama3.2:1b;
these observations are specific cases, not a guarantee for every model output.

Live acceptance on qwen3:4b: ordinary explanation without tools; natural-language
web search with a real search result; tools disabled; rejected search without
execution; read → web search → create chain without mode switching. Reports are
under `local-output/b-acceptance/automatic-tools.json` in the verification setup.


Regression check: the request "Read project_notes.md. Find official documentation
for the database mentioned in that file. Create a new report containing the
project language, database and documentation URL" completed three consecutive
live runs with qwen3:4b after the workflow transition fix. Each run had exactly
read/search/create observations and a separately verified file. This addresses
the failure where a prose `{name: Search, arguments: ...}` envelope was exposed
as the final answer after only reading the file.


## T4 permission evidence

`python -m file_access.verify` passed all 12 real-MCP checks, including per-path grants, explicit denial, creation without overwrite and version-checked updates. The normal web chat was also exercised with qwen3:4b: create → read → approved update → read. Full regression suite: 456 tests and 170 subtests passed on 4 November 2026. See [File permissions](FILE_PERMISSIONS.md) for code entry points, commands, and the in-place update durability limitation.


Explicit single-tool commands are handled by `parse_explicit_action` and
`run_explicit_action` before model planning. Adapters should pass `user_request`
with the actual current user text to `run_optional_turn` / `run_turn`, separate
from assembled history and retrieved content. They preserve the standard schema,
policy and approval boundary. See [File permissions](FILE_PERMISSIONS.md#exact-user-selected-tool-calls)
for syntax and regression evidence.


## Persistent MCP connections

See [MCP connections and demonstration](MCP_CONNECTIONS.md) for the current web lifecycle manager, runtime registration, independent SDK interoperability check, and third-party Time demonstration. The web now reuses connections between turns; standalone CLI calls still own short-lived sessions. This supersedes earlier per-turn web connection guidance.
