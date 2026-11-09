# MCP connections and demonstration

Sensai uses the official MCP Python SDK to connect to independent servers over **stdio or Streamable HTTP**. The SDK performs initialization, capability/version negotiation, the initialized notification, tool discovery and JSON-RPC calls. The web application keeps these sessions open across chat turns. A separate owner task manages each SDK session and closes it during application shutdown.

Both transports use the official MCP session and JSON-RPC implementation. Process separation is not an OS sandbox: the Python execution tool has its own isolation mechanism, while other installed MCP servers run with the local operator's permissions.

## Start

From the repository in Ubuntu/WSL:

```bash
source .venv/bin/activate
python -m pip install -r requirements-mcp.txt
ollama pull qwen2.5:7b
python deploy/start_b.py --port 8020 --model qwen2.5:7b
```

Open http://127.0.0.1:8020. Use **Chat**, `qwen2.5:7b`, and **Allow tools**. The launcher also starts SearXNG on port 8088 and waits for it to become ready. An existing healthy SearXNG is reused. Ctrl+C stops only processes started by this launcher. On first-time search setup use `python deploy/start_b.py --port 8020 --install-search`.

## Manage connections

Click **Tools**. Each server card shows status, identity/version reported by the server, negotiated MCP version, connection generation, available tools, argument schemas, approval rules, server-provided usage notes and recent connection/call events.

| Action | Observable behavior |
| --- | --- |
| Connect | Starts a configured stdio process or connects to an HTTP endpoint and negotiates a real MCP session. |
| Close | Closes its transport (and owned stdio process) and removes its tools. An HTTP server keeps running independently. Chat does not reconnect it automatically. Connect restores it. |
| Restart | Closes the old session and connects again; a stdio process is replaced, while an HTTP server receives a new client session. Tools are rediscovered. Old pending approvals cannot execute against the replacement connection. |
| Disable | Closes the connection and removes its tools; the disabled state survives application restart. |
| Enable | Restores the connection and tools. |
| Remove | Closes and removes this server from the local registry. |

The first chat may connect initially configured servers. A failed optional server is shown as failed and does not remove healthy servers' tools. Timeout/transport failure is not silently retried: a side effect may already have happened. Review the error and restart explicitly.

## Add any supported MCP server

**Tools → Add an MCP server** accepts a conventional `mcpServers` mapping, a Sensai
`servers` list, or a single object with `name`. Paste connection settings from the
server's documentation; no Python adapter or feature-specific branch is required.
You may import several connections in one submission. An invalid or duplicate
entry rejects the whole import before any new connection is registered.

For a local stdio server, supply the actual executable and argument array:

```json
{
  "mcpServers": {
    "my_service": {
      "command": "/absolute/path/to/server-executable",
      "args": ["--some-server-option", "value"],
      "env": {"API_KEY": "replace-with-the-server-credential"}
    }
  }
}
```

The executable must exist **where Sensai runs**. When Sensai runs in WSL, Windows
installation paths do not become Linux executables. Install the server and its
dependencies there, or explicitly use the appropriate executable bridge. Sensai
does not install packages automatically. `$PYTHON` selects Sensai's current Python
environment. `cwd` is optional; paths resolve against the configuration file's
directory, or the application's working directory for a web import. `env` adds
explicit string values to the SDK's minimal inherited environment. Values are
literal: shell expressions and `${...}` are not expanded.

For an independently running Streamable HTTP server, use its MCP endpoint:

```json
{
  "mcpServers": {
    "my_remote_service": {
      "type": "http",
      "url": "https://your-server.example/mcp",
      "headers": {"Authorization": "Bearer replace-with-your-token"},
      "timeout": 30
    }
  }
}
```

Omit `headers` when authentication is not required. `transport: "http"` and
`transport: "streamable-http"` are also accepted. This is an MCP endpoint, not an
arbitrary REST API or a legacy `/sse` endpoint. Timeout is configurable from more
than zero to 300 seconds; the default is 15 seconds.

Click **Add connections**, then **Connect**. The SDK performs `initialize`, the
initialized notification and paginated `tools/list`. Sensai builds names, descriptions
and argument schemas from that response. **Do not type the catalogue manually.**
If `allow_tools` is omitted, discovered tools become available and every call
requires approval by default. An optional explicit allowlist remains supported;
an empty list deliberately disables all tools. `auto_approve` is always explicit.

Each connected tool has **Off / Ask every time / Automatic**. Click **Save tool
permissions** to apply the selection. Off removes it from model planning and
execution, but keeps it visible in the connection panel so it can be re-enabled.
Saving permissions reconnects the session and invalidates pending approvals.
Names sent to the model are namespaced per server. Long names or names containing
dots receive stable aliases; calls still use the server's original tool name.
The panel shows both names and the discovered schema.

Changes are saved to `local-output/mcp-registry.json` (ignored by Git, owner-only permissions on Linux). Environment values and HTTP credentials are stored there; they are not returned by the status API or included in model prompts. Do not share this registry. This becomes the runtime registry after the first change. `SENSAI_MCP_STATE` can select a separate registry. Initial seeds still come from `SENSAI_MCP_CONFIG`. Restarting Sensai restores the saved registry; closing alone does not persist an off switch, so use Disable to keep a server off across restarts.

Management writes are restricted to local clients with same-origin JSON requests. This is a local single-operator product setup, not an authenticated public management service. A production remote deployment needs authentication, authorization and an administrative configuration boundary.

## Demonstrate an existing external server

`mcp-server-time` is a third-party package installed from `requirements-mcp.txt`. Its implementation is not part of Sensai. In Tools, disable the original `time` connection so there is no competing alias. Expand **Add an MCP server** and submit:

```json
{
  "mcpServers": {
    "external_time": {
      "command": "$PYTHON",
      "args": ["-m", "mcp_server_time"]
    }
  }
}
```

Click **Connect**. The card should identify `mcp-time` and list its schemas. Start a new conversation and send:

```text
Get the actual current time in Europe/Madrid using the connected time server.
Then convert that returned time to Asia/Tokyo using its conversion tool.
Report both observed times.
```

Inspect and approve each proposed call. Expected sequence:

1. The model chooses `external_time__get_current_time` with `Europe/Madrid`.
2. The real result contains the current datetime.
3. The next model proposal uses the observed time as input to `external_time__convert_time`.
4. The conversion result appears and the response continues through output checks.

This natural-language example demonstrates model selection and result-dependent continuation. A `Perform exactly one tool call...` request is a separate explicit-user-action path and should not be used as proof that the model selected the tool.

Reopen Tools and show Close → Connect → Restart → Disable → Enable. Disable removes those tools from subsequent chat turns. An already-displayed approval from an earlier connection is invalid after restart/disable. The per-message **Allow tools** switch bypasses the tool stage without changing server configuration.

## Blender as an interoperability example

Blender does not have an adapter in Sensai. Install its server and Blender-side
add-on using the [upstream instructions](https://github.com/ahujasid/mcp-for-blender),
then paste the normal executable configuration into the same connection panel:

```json
{
  "mcpServers": {
    "blender": {
      "command": "/absolute/path/to/blender-server-venv/bin/mcp-for-blender",
      "env": {"BLENDER_MCP_DISABLE_TELEMETRY": "1"},
      "timeout": 60
    }
  }
}
```

The server must be able to reach the running Blender add-on. A connected MCP
session alone does not prove that Blender is running or that scene operations
succeeded. After starting Blender and its add-on, try this in a new Chat:

```text
Create a cube named SensaiDemoCube in Blender. Then inspect the scene to verify
that the object exists and report its actual type and dimensions.
```

Inspect the proposed arguments before approving. Evidence requires the actual
creation result followed by a scene observation and the object in Blender.
Do not treat a model's sentence as proof of creation. Third-party server code
runs with its own permissions; it is not inside Sensai's Python execution sandbox.

Verified in this change: the installed `mcp-for-blender==2.2.7` connected through
the same web import as Time, negotiated MCP `2025-11-25` and exposed nine tools,
including `get_scene_info` and `execute_blender_code`, without a manual allowlist
or a Blender-specific code path. **Scene creation was not executed in this check.**

## Supported scope

Supported: stdio, Streamable HTTP (JSON and SSE response bodies), explicit
environment/HTTP credentials, lifecycle management, paginated tool discovery,
schema validation, per-call approvals and text/structured results. Non-recursive
local JSON Schema references are resolved without fetching external documents.

Not implemented: legacy HTTP+SSE transport, an interactive OAuth login flow,
image/audio result rendering, resource subscriptions and server-initiated sampling.
Recursive/external schemas and oversized catalogues/results are rejected visibly.
The current bounds are 16 connections, 128 tools per server, eight discovery pages,
32 KiB per expanded schema, 256 KiB combined catalogue, 16 KiB arguments and
64 KiB result. New server capabilities outside this subset need further support;
there is no claim that every possible MCP extension is implemented.
Tool lists are refreshed by reconnecting/restarting, not automatically on
`notifications/tools/list_changed`.

## Show our server working with another client

```bash
python examples/mcp_sdk_client.py
```

This program imports only the official MCP SDK and Python standard library. It starts `mcp_tools.project_server`, negotiates a session, lists tools, reads the sample notes and prints a PASS result. It does not import Sensai's gateway, runner, web API or model.

Other MCP hosts can launch the same server. Example configuration for a host running inside WSL (replace paths with your checkout):

```json
{
  "mcpServers": {
    "sensai-project": {
      "command": "/home/markc/G-AIA-500-BAR-5-1-sensai-2/.venv/bin/python",
      "args": ["-m", "mcp_tools.project_server", "--workspace", "/home/markc/G-AIA-500-BAR-5-1-sensai-2/examples"],
      "cwd": "/home/markc/G-AIA-500-BAR-5-1-sensai-2"
    }
  }
}
```

That configuration is illustrative; the independent SDK client is the interoperability check actually executed. File permissions are enforced by the server. Per-call human approval belongs to the connecting host.

## Inspect the loop and code

Chat displays MCP start/observation/completion cards, exact approvals, call arguments and actual results. These use the existing event store and can also be inspected with `GET /api/runs/{run_id}/history`. They show decisions and observations, not private model reasoning. Connection metadata and the bounded connection event history are available in Tools and `GET /api/mcp/servers`; connection history is in-memory, while per-run events are stored by the existing application.

| Entry point | Responsibility |
| --- | --- |
| `mcp_tools/config.py::parse_config` | Validate generic stdio/HTTP settings and import `mcpServers`. |
| `mcp_tools/gateway.py::connect` | Official SDK transport, initialization and schema discovery. |
| `mcp_tools/manager.py::Connection.worker` | Own one persistent process/session, serialize calls, propagate cancellation. |
| `mcp_tools/manager.py::ConnectionManager.control` | Close, restart, enable, disable, remove and persistence. |
| `mcp_tools/manager.py::ConnectionManager.set_tools` | Apply per-tool permissions and invalidate the old generation. |
| `mcp_tools/manager.py::ManagedGateway.call` | Refuse stale connection generations before execution. |
| `mcp_tools/runner.py::connection_scope` | Shared managed session in web; short-lived SDK connection for standalone CLI. |
| `mcp_tools/runner.py::run_optional_turn`, `tool_loop` | Model action generation → schema/policy validation → approval → actual call → observation → continuation. |
| `mcp_tools/runner.py::review_execution` | Check application payloads after each call, including errors inside `isError=false`. |
| `mcp_tools/runner.py::review_workflow` | Assess actual results against the request, then plan bounded corrections and independent verification. |
| `mcp_tools/web.py::install_routes`, `turn_events` | Lifecycle API, application shutdown, per-run approvals and events. |
| `web/static/mcp_connections.js` | Connection management and schema/protocol display. |

## Verification

```bash
python -m pytest tests/test_mcp_feedback.py tests/test_mcp_outcome.py tests/test_mcp_context.py tests/test_mcp_connections_generic.py tests/test_mcp_manager.py tests/test_mcp.py tests/test_mcp_web.py tests/test_explicit_mcp.py tests/test_b_integration.py -q
python examples/mcp_sdk_client.py
python -m mcp_tools.verify
```

Manager tests use real processes and include process reuse, PID replacement after restart, child termination, serialized concurrent calls, cancellation, stale approvals, disabled-state persistence, independent third-party Time and failure isolation. Web tests additionally cover management endpoints, cross-origin/remote refusal, tool disabling and normal approval flow.

The current baseline (`982a94d`) already has 13 failures in the full suite, reproduced in a clean archived checkout: CLI semantic-cache assumptions, obsolete CLI mocks, document upload using the removed SQLite `chunks` table, and two retrieval subtests. MCP-focused results must be reported separately from that broader suite. The `usage` event contract and removed session-refresh callback were repaired here because they prevented the live web workflow from completing.

Protocol references: [MCP lifecycle](https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle), [standard transports](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports).

Current focused verification: 109 MCP/integration tests and 62 subtests passed. Existing Pydantic/Chroma deprecation warnings remain.
Generic transport tests start actual independent processes: stdio with environment
values and nested schemas, plus an authenticated HTTP server exercised with both
JSON and SSE responses. They check discovery, calls, result-to-model continuation,
namespace separation, policy changes, credential omission from status, persistence,
auth failure, and remote-server survival when its client connection closes.
Model proposals in automated tests are controlled; these are not model accuracy scores.

Live browser verification separately imported Time and Blender from one
`mcpServers` object without tool lists, connected both, disabled and re-enabled an
individual Time tool, completed a model-selected actual time call with `qwen3:4b`,
and exercised Close/Connect/Restart/Disable/Enable. No JavaScript errors occurred.
The two third-party catalogues are real; only the Time action was executed in this
browser check.


### Model routing checks

The launcher default remains `qwen3:4b`. For the Blender demonstration, pass
`--model qwen2.5:7b` and select the same model in the browser. A saved session can
retain its previous model selection. The current
time-to-conversion scenario was checked in the browser with prior conversation
history: both calls succeeded and the conversion argument came from the actual
first result. The **MCP · plan** card records the selected model, available tool
names and the proposed steps. An empty plan receives one bounded review with an
explicit answer-versus-execution decision before falling back to ordinary chat.

Smaller installed models are not equally reliable: `llama3.2:1b` supplied an ISO
datetime where the third-party time server required `HH:MM`, and `qwen3:0.6b`
missed a web-search request in a routing check. A successful example does not
establish reliability across models. Keep approval enabled, inspect actual tool
results, and rerun acceptance checks when changing the model.

### Server guidance and exact local operations

The client's initialize response now preserves bounded `instructions` from each
server. These usage notes reach both workflow selection and the tool loop as
explicitly untrusted capability data. They cannot authorize extra actions or
override per-call consent. Disabled servers contribute neither tools nor notes.
The connection panel exposes the notes for inspection.

Tool selection compares capabilities rather than names alone. Exact local edits
should use native application operations or scripting when available; an
approximate paid generator is not a prerequisite for constructing a primitive.
Verification should inspect the resulting state rather than repeat a mutation.
This is general routing guidance, not a Blender-name dispatch rule.

MCP requests explicitly set Ollama `num_ctx=16384` by default and reserve an output
budget of 2048 tokens. Override with `SENSAI_MCP_NUM_CTX` (4096–131072) when the
hardware/model supports it. A response whose reported prompt usage reaches the
context threshold is rejected before its proposed action can execute. This
conservative threshold is not an exact tokenizer or a guarantee against all
provider-side truncation. Reduce history/enabled tools or increase context when
the limit is reported. More context can increase memory use and latency.

The final synthesis no longer asserts that every tool call succeeded merely
because `isError` was false. It must inspect the payload; some third-party tools
return errors as ordinary text. Sensai preserves that actual payload and does not
rewrite arbitrary text into protocol errors.

Live routing check with qwen3:4b: the plain-English cube request selected local
application code followed by scene inspection in three runs (one with Blender
alone, two with the full 14-tool catalogue). No asset-generation tool was selected.
These checks generated plans only: Blender's scene connection was not running,
so they do not establish end-to-end cube creation.


### Check the requested outcome, not just transport success

The planned workflow now assesses the actual observations before publishing an
answer. The model returns structured requirement checks (requested property,
observed value, match) and a short factual summary. Code derives completion from
those checks. When requirements remain unmet, a separate planning stage can
propose a correction using the enabled catalogue. This is generic application
logic; no Blender-specific tool mapping or cube code is embedded in the client.

The default budget is four actual tool calls for the entire turn, including
corrections. A correction plan must contain a change followed by a separate
inspection, or a single inspection if only evidence is missing. With one call
left, a new mutation cannot be proposed. Every action still passes argument and
policy checks and its configured approval rule. Rejecting a correction stops the
workflow; earlier actions are not undone. The exact code remains visible for
review: a third-party scripting tool is not mechanically read-only just because
the model labels its step as inspection.

The web records **MCP · review** with the requirement checks and correction plan.
Malformed assessments and model responses that hit the output limit are rejected;
unfinished model text is not released as an answer. An unmet goal without a valid
correction is reported as incomplete. A model judgment can still miss a requirement
or choose an ineffective operation; inspect the actual results. This mechanism
does not prove arbitrary domain correctness or guarantee successful recovery.

Live verification on 2026-11-09 used qwen3:4b, the complete enabled 16-tool
catalogue, and the independent mcp-for-blender server with a running Blender
4.5.15 scene. On a uniquely named temporary cube, the first action was deliberately
changed by the test harness to create dimensions 1×1×1 instead of the requested
2×2×2. The model then selected scene inspection, a correction of that existing
object, and a separate inspection. Actual scene output confirmed 2×2×2. A further
independent readback confirmed the result; the harness removed only its temporary
object. The user's original cube was not changed. Evidence is stored locally in
`local-output/blender-recovery-live/report.json` and is not committed.

This is one successful end-to-end recovery check, not a reliability benchmark.
Other development trials stopped without a valid correction; the application must
surface such failures rather than claim success. Automated tests additionally
exercise a real independent stateful MCP process with controlled model proposals:
wrong-but-successful result, approved correction, rejected correction, exhausted
budget, invalid assessment, missing independent verification and truncated output.

To check the existing demonstration object after restarting Sensai, use a new Chat
with tools enabled, keep Blender open, and send:

```text
Inspect the existing object SensaiDemoCube in Blender.
Set its dimensions to exactly (2, 2, 2) and its location to (0, 0, 0).
Do not create another object.
After the change, inspect the scene again and report the actual dimensions and location.
```

Review and approve the calls. The final scene observation must identify the exact
object, dimensions 2×2×2 and location (0, 0, 0). The final prose alone is not evidence.


### Execution failures and conditional steps

Each planned step has an explicit `always` or `if_needed` condition. Conditional
repair is assessed against actual results before execution. It is skipped when
the request is already satisfied; writing "if needed" in a step description does
not authorize an unconditional mutation.

After each call, `review_execution` assesses the operation's actual payload.
Some external servers return application exceptions inside `isError=false`; the
wire result remains visible and unchanged, while **MCP · execution_review** shows
the separate application assessment. This judgment is model-based and can be
wrong; it does not replace server-side permissions or execution isolation.

A failed operation may regenerate changed arguments using its actual error and
the original tool schema before dependent steps continue. Failed inspection can
retry only inspection. Mutating retries require at least two calls remaining in
the turn budget. Every retry still requires its configured consent, and an
unchanged failed call is blocked before approval/execution. In a correction step,
identical previously completed code is also blocked to avoid replaying creation.
Transport exceptions, explicit protocol errors and permission denials still stop
the planned workflow; they are not blindly retried. Earlier side effects are not
rolled back, so corrections must account for partially created targets.

Argument generation receives a separate current-step context. Previous proposals
are data rather than messages to imitate. Once one tool is selected, generation
uses that tool's discovered argument schema directly. A failed argument/program
gets a focused correction prompt with the actual error instead of replaying the
entire original creation request.

Original Qwen3 templates need care: the installed `qwen3:4b` template always opens
`<think>`, and live tests observed reasoning text even with `think=false`. Sensai
uses Qwen's documented empty-thinking assistant prefill for original Qwen3 names
on non-thinking MCP requests. It is a wire-only compatibility step, not persisted
history or tool evidence. Instruct tags are excluded. Runtime override:
`SENSAI_MCP_NO_THINK_PREFIX=auto|on|off` (default `auto`; `on` also supports a renamed
original Qwen3 model). See the [Qwen documentation](https://github.com/QwenLM/Qwen3/blob/main/docs/source/inference/transformers.md#thinking--non-thinking-mode).

This compatibility change does not make a small model reliable at arbitrary
application code. Further live fresh-object trials with both `qwen3:4b` and
`qwen3:4b-instruct` still produced obsolete Blender calls or invalid inspection
fields. Do not describe those trials as successful creation demonstrations.
The local per-trial reports are under `local-output/blender-fresh-live/`; every
trial uses its own temporary object and records cleanup.


### Fresh-object check with a live model

After simplifying the active-step argument context, three consecutive local
`qwen2.5:7b` runs created uniquely named cubes with dimensions `(2, 2, 2)` at
`(8, 0, 0)`. Each request used natural language without tool names. The model
selected the tool and generated every proposed argument; no action was injected
or corrected by the harness. The full enabled 16-tool catalogue was available.

The model printed the object's actual properties within its creation call. A
separate harness call to the external server independently read the resulting
scene and checked its dimensions and location. These runs do **not** demonstrate
that the model always chooses a separate verification call itself. Every test
used a new temporary object, and the harness removed that object afterward.
Reports are kept locally under `local-output/blender-fresh-live/`:

- `SensaiFreshCheck_9ab2d395/report.json`
- `SensaiFreshCheck_bb4cd486/report.json`
- `SensaiFreshCheck_345108df/report.json` (final planning prompt)

Earlier development trials with larger, mixed current/history contexts failed,
including a `qwen2.5:7b` trial. Three successful checks are a reproducible local
example, not proof of reliability for arbitrary Blender operations. Review the
actual code and results; model judgments can misclassify an operation.

Restart the web process after updating the code, choose the model explicitly,
and use a new chat. Keep Blender and its addon running. For another manual check:

```text
Create a new cube named SensaiFreshCube02 in Blender. Its dimensions must be
exactly (2, 2, 2), and its location must be (4, 0, 0). Do not modify any existing
objects. After creating it, inspect the scene and verify its name, dimensions and
location. If anything is incorrect, correct only this object and verify again.
Report only actual verified values. Do not give me instructions or code to run.
```

Approve only code consistent with the requested new object. Use a new unused name
for every repeat; a same-name object already in the scene is a different test.


### Answers and source results

The common answer stage summarizes actual MCP observations in the user's language.
It should answer the question directly, including relevant values, units and
direction. Raw JSON is shown as an answer only when requested. There are no
server-name branches, city mappings or timezone arithmetic in this presentation
change. The original tool payload remains unchanged in the result card and event
history. The existing supplied-Python execution report still renders its verified
stdout directly. Model summaries can be wrong; the actual tool result remains
the evidence to inspect.
