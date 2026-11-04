# T4 — File access within permissions

The operator selects a workspace and grants operations on specific files or directories. The application checks these grants before accessing an agent-requested target. Model output and user approval cannot expand those grants.

## Policy

`configs/files.scoped.json` is a concrete example:

```json
{
  "extensions": [".txt", ".md", ".py", ".json"],
  "max_bytes": 16384,
  "allow_create": true,
  "permissions": {
    "read": ["project_notes.md", "src/", "reports/"],
    "create": ["reports/"],
    "write": ["reports/"],
    "deny": ["src/private/", "reports/protected.md"]
  }
}
```

- `src/main.py` is an exact file grant. `src/` includes descendants; it does not match `src2/`.
- `.` grants the entire selected workspace for that operation. All paths remain relative to the workspace.
- Read, create and write grants are independent. `deny` overrides every grant.
- Create never replaces an existing file. Write updates an existing file only and requires its latest SHA-256 version.
- Empty grant lists disable that operation. `allow_create: false` disables creation even with create paths.
- When `permissions` is present, all four lists are mandatory. Invalid configuration fails closed.
- Extension/size restrictions apply in addition to permissions. Hidden paths, traversal, symlinks, hardlinks and special files remain disallowed.

Legacy policies without `permissions` retain workspace-wide reading, optional creation and no updates. The integrated demo uses `configs/files.demo.json`: workspace-wide reading/creation, updates only inside `reports/`, and explicit denial of `private/` and `reports/protected.md`.

## Where enforcement lives

| Code | Responsibility |
| --- | --- |
| `file_access/policy.py` — `FilePolicy.from_file`, `permits` | Parse operator grants; decide operation/path permission without filesystem I/O. |
| `file_access/reader.py` — `WorkspaceReader._authorize`, `_open_parent`, `read_text` | Validate before target access; walk relative directory descriptors with `O_NOFOLLOW`; bounded UTF-8 reads. |
| `file_access/writer.py` — `prepare_create`, `decide` | Bind the exact new path/content to one decision; recheck permission; publish without overwrite. |
| `file_access/writer.py` — `prepare_write`, `_decide_write` | Check write grant, expected hash and inode; recheck before mutation; consume each decision once. |
| `mcp_tools/project_server.py` — `read_project_file`, `create_project_file`, `write_project_file` | Expose the same permission-enforcing operations over real MCP. Reads return content and SHA-256. |
| `web/app.py` — `get_workspace_writer` | Legacy web operations use the same reader/writer; no permissive fallback. |
| `tests/test_file_permissions.py` | Independent operation grants, denial before I/O, stale versions, replay, path swaps, links and actual MCP calls. |
| `tests/test_mcp_web.py` — `test_normal_chat_file_update_requires_approval_and_respects_policy` | Actual web approval and MCP execution; denied approval does nothing; approved forbidden writes still fail. |

The workspace is opened once during operator-controlled startup. Internal descriptor traversal, version inspection and temporary publication are part of the authorized operation. Application databases and configuration loading are not agent-requested file operations.

## Reproduce the checks

From Ubuntu/WSL:

```bash
cd ~/G-AIA-500-BAR-5-1-sensai-2
source .venv/bin/activate
python -m file_access.verify
python -m pytest tests/test_file_permissions.py tests/test_file_access.py tests/test_file_write.py tests/test_mcp_web.py -q
```

The verifier uses an actual MCP child process and temporary fixtures, without an LLM or mocked server. It prints 12 checks and saves `local-output/t4-permissions.json`; a failed check returns a nonzero exit status. Inspect both the denial response and unchanged file content. The test suite additionally verifies no target `open`, `stat` or `read` occurs for permission-denied requests and exercises approval rejection.

## Show it in the web interface

Restart the server after code/configuration changes:

```bash
python deploy/start_b.py
```

Open the displayed URL. Use **Chat**, **Allow tools** enabled and `qwen3:4b`. No search service is required for these file-only examples. Use a new filename on each rehearsal.

1. Send: `Create reports/t4-demo-01.md with exactly this content: T4 original`.
2. Inspect the proposed path and content, then **Approve**. Expand the actual tool result.
3. Send: `Read reports/t4-demo-01.md now to obtain its SHA-256. Then update that existing file to contain exactly: T4 updated. Use the returned SHA-256 for the write.`
4. Approve the read. Inspect the next approval: it must contain the existing path, replacement content and observed SHA-256. Approve the update.
5. Check the actual file in a terminal:

   ```bash
   cat local-output/reports/t4-demo-01.md
   ```

   It should contain `T4 updated`.
6. Request another update, but **Reject** its write approval. Check the file again: unchanged.
7. Show the verifier's `write_readonly_file_denied`, `explicit_deny_wins` and `stale_version_denied` results. These make the failure paths deterministic rather than depending on whether the model attempts a forbidden action.

## CLI and configuration

For legacy CLI reads and create-only `/save`, select the same policy explicitly:

```bash
python chatbot.py --model qwen3:4b --workspace local-output --file-policy configs/files.scoped.json
```

For MCP read/create/update:

```bash
python chatbot.py --model qwen3:4b --mcp-config configs/mcp.integrated.json --guardrails guardrails/demo.json
```

Use `/mcp` before the requests in this CLI. The browser selects tools automatically in Chat. The project server's `--workspace` and `--policy` arguments in the MCP configuration define its boundary; the separate CLI `--file-policy` does not override that server configuration. For the legacy web file adapter use `SENSAI_FILE_POLICY` alongside its configured workspace.

Changing grants is an operator configuration action, not an instruction the model can execute. Restart after changing configuration. All file tools must remain outside `auto_approve`; update the MCP `allow_tools` list when enabling/disabling operations.

## Guarantees and limits

Approval is consent for exact arguments; permission is the maximum allowed scope. Both are required in the application. Direct local SDK calls in the verifier intentionally bypass interactive consent, but cannot bypass file permissions.

Updates check content version and file identity and use a descriptor plus an advisory lock. They are in-place writes, not crash-safe transactions: a storage failure may leave partial changes, which is reported as an error. Locks serialize cooperating writers; this is not isolation from a hostile local process with the same OS privileges. No delete/move tools or automatic system-folder access are exposed. Unsupported platforms fail closed; run these filesystem tools in Linux/WSL.


## Exact user-selected tool calls

An explicit command with a complete JSON argument object bypasses model planning
and argument generation. It still uses discovered schemas, the operator tool
allowlist, content policy, normal approval and the server's file permissions.
Only the current top-level user request is parsed; quoted examples, retrieved
documents, model responses and previous turns are not executable commands.

In ordinary Chat, send this exact request:

```text
Perform exactly one tool call: project__create_project_file
Arguments: {"relative_path":"reports/protected.md","content":"This creation must be denied"}
Do not read or update any file.
```

The only approval must be for `project__create_project_file` with those exact
arguments. Approve it: the actual server result must say
`Permission denied for create: reports/protected.md`. No substitute read or write
is attempted, including after failure. Existing chat history need not be cleared.

Also supported: `Call server__tool with arguments: {"key":"value"}` and
`Call project__create_project_file with relative_path "reports/new.md" and content "Example".`
Inline values must be valid JSON literals. Malformed recognized commands fail
instead of falling back to model interpretation. Successful explicit calls render
the actual result without asking the model to invent a summary. Natural-language
workflows continue through the ordinary model planner; this exact-command contract
does not promise deterministic interpretation of arbitrary prose.

Code: `mcp_tools/explicit.py::parse_explicit_action`,
`mcp_tools/runner.py::run_explicit_action`. Both normal web Chat and the CLI MCP
entry point use the same validation/approval/execution loop. Privacy filtering
that would change exact arguments produces an error instead of a silent rewrite.

Regression checks: `python -m pytest tests/test_explicit_mcp.py tests/test_mcp_web.py -q`.
The exact failing user request was verified in a real browser after a prior file
read. Full suite after this fix: 465 tests and 176 subtests passed, with one existing
Pydantic deprecation warning (4 November 2026).
