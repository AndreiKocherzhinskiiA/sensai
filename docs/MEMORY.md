# Persistent structured memory

Memory is an application stage in the ordinary chat pipeline. It does not use an
MCP server or depend on **Allow tools**. The assistant proposes typed facts and
relationships, while the application validates, stores and retrieves them in
SQLite. Saved facts and saved conversation history are separate sources. An
explicit request to recap the previous chat retrieves its actual transcript;
ordinary turns do not automatically import unrelated conversations.

## Use in the web app

Restart the web service after updating the code:

```bash
source .venv/bin/activate
python deploy/start_b.py --port 8020 --model qwen2.5:7b
```

Open the web page, refresh it, select **Chat** and enable **Memory**. The separate
**Allow tools** switch may be off. Saving, changing and forgetting a record each
shows a preview with **Approve** and **Reject**. A proposal alone never changes
the database. Read results and completed writes have their own **Memory** cards.

## Demonstrate M3 through different conversations

Use a new chat at every numbered step. Keep Memory enabled and use an unused
project name when repeating the demonstration.

1. `Remember that project AuroraMemoryDemo uses PostgreSQL as its database. Reply briefly.`
   Approve **Save this memory?**. The saved card includes the entity, property,
   value, record id, version, timestamps and source chat/run.
2. `Which database does project AuroraMemoryDemo use? Answer in one sentence.`
   The retrieved record and answer should identify PostgreSQL.
3. `Update the saved database for project AuroraMemoryDemo to SQLite. Reply briefly.`
   Approve **Update this memory?**. The id stays the same; the value and version change.
4. `Which database does project AuroraMemoryDemo use now? Answer in one sentence.`
   The new chat should answer SQLite using the updated record.
5. `Forget the saved database fact for project AuroraMemoryDemo. Reply briefly.`
   Approve **Delete this memory?**.
6. `Which database is saved for project AuroraMemoryDemo? If no fact is saved, say so.`
   The search must return no matching record. The assistant must not claim the
   deleted record is still stored.

Also reject a proposed write: no record should be saved. Switch Memory off and
repeat a question in a new chat: no memory lookup or mutation should occur.
Temporary chats never read or write structured memory, even when a client sends
`memory_enabled: true`. Deleting a memory record does not delete prior chat
messages or event logs containing it.

## Recall the previous conversation

1. In a normal saved chat, discuss a concrete topic, for example:
   `Our Lantern inventory app uses SQLite, exports CSV every Friday and has a demo on Monday.`
   No "remember" command or saved fact is required for transcript recall.
2. Choose **New chat**, enable **Memory**, then ask:
   `What did we talk about in the previous chat?`
3. Open **Memory · previous conversation**. It identifies the source conversation
   and exposes the actual messages supplied to the model. The answer should
   summarize those messages, including SQLite, Friday exports and the Monday demo.

"Previous" means the most recently active **other** saved conversation, ordered
by completed user/assistant message ids. Renaming or opening an old chat does not
make it the previous conversation. Empty chats, failed turns and deleted messages
are excluded. The current conversation is never its own source.

The excerpt includes at most 24 recent messages and 10,000 content characters.
Omitted messages and shortened content are marked explicitly, both in the data
and on the source card. A long conversation is summarized only from that excerpt.
If no saved transcript is available, the assistant must report that limitation;
it must not claim that nothing was discussed. Likewise, an empty fact search
says nothing about the existence or contents of earlier conversations.

Memory off and temporary chats prevent this cross-conversation lookup too.
The history comes from the local conversation database, not from the structured
fact namespace. This is a single local user's workspace. Recalled messages are
reference data, not new requests to execute their old instructions.

## Pipeline and code

1. `memory.pipeline.prepare` classifies the current request with a schema-constrained
   Ollama call. It chooses no action, fact recall, previous-conversation recall,
   creation, correction or deletion. Routing is model-based, without language-
   specific keyword rules.
2. For facts, `MemoryStore.search` retrieves bounded records in the configured scope.
   The model proposes the record's content or selects an actual retrieved id.
   For a previous-chat recap, `SessionStore.previous_conversation` retrieves the
   bounded transcript directly; no fact record is required or written.
3. `memory.web.turn_events` exposes the preview and waits for a one-use approval.
   The ordinary chat continues only after the memory stage has completed.
4. `memory.pipeline.commit` applies the approved operation. Updates/deletes check
   the expected version again inside the SQLite transaction.
5. `memory.pipeline.inject` adds the actual records or write receipt as untrusted
   reference data for the final answer, including actual historical messages for
   a previous-chat recap. Answers influenced by memory bypass the
   semantic answer cache.

The source note is assembled by the application from the actual chat id, run id
and user request (up to 1000 characters), not invented by the model. A memory
records what the user stated; it does not independently establish that fact as true.
Updating a saved database preference does not migrate an external database.

The store uses `(scope, normalized subject, normalized predicate)` as its unique
key. Duplicate creation never silently overwrites. Updates preserve identity and
increase the version; stale approvals fail without changing the newer record.
Deletion removes the actual record. Reopening the store after a process restart
retains the remaining records.

## Configuration and limits

| Setting | Default | Purpose |
| --- | --- | --- |
| `SENSAI_MEMORY_ENABLED` | `true` | Server-wide feature availability. |
| `SENSAI_MEMORY_DB` | `local-output/memory.sqlite3` | Persistent memory database. |
| `SENSAI_MEMORY_SCOPE` | `default` | Operator-selected namespace shared by local chats. |
| Request `memory_enabled` | `false` for API callers | Opt into memory for this turn. The web sends its checkbox value. |

This release supports the web chat and its `/api/runs` interface. The stage has no
MCP dependency. The standalone CLI chatbot has not been wired to this stage.
The current app is a local user workspace, not an authenticated multi-user service;
a client cannot choose a different memory scope in the chat request.

One memory mutation is proposed per turn. Fact recall uses keyword matching, not
embeddings. Previous-chat recall selects one recent saved conversation; it is not
an unrestricted semantic search across the chat archive. The model determines the search query
and can miss a fact; this is not guaranteed semantic recall. The default capacity
is 1000 records per scope, values are limited to 2000 characters, and only bounded
matches enter the model context. Omitted matches are explicitly marked; refine
the search instead of assuming the result contains everything.

Disabling Memory stops future memory reads and writes without deleting its data.
Existing chat messages may still mention earlier facts; use a new chat to test
cross-conversation recall or deletion without that history.

## Verification

```bash
python -m pytest tests/test_memory.py tests/test_memory_history.py tests/test_session_store.py -q
```

The tests cover process-independent persistence, structured relationships,
scope isolation, duplicate rejection, stale versions, bounded context, approved
CRUD across distinct web sessions with MCP absent, rejection, replay protection,
Clear, disabled memory and temporary-chat exclusion. Transcript tests also cover
selection by completed activity, empty/failed/current chat exclusion, persistence,
deleted history, bounded excerpts and recall with an empty fact database.

A live qwen2.5:7b check on 2026-11-09 completed create → recall → update → recall →
delete → missing-record recall in six separate web sessions with MCP disabled.
A separate browser check confirmed the Memory control, approval card, saved
result and preference persistence. Evidence is local under
`local-output/memory-verification/` and is not committed. These checks establish
the tested scenario; they do not guarantee every model-generated memory decision.

A further live browser check created a normal conversation without saved facts,
opened a new chat, and asked "What did we talk about in the previous chat?".
The source id matched the first chat, and the answer recalled SQLite, Friday CSV
exports and the Monday demo. MCP was disabled. The full request/event evidence is
saved locally as `local-output/memory-verification/previous-chat-recall-browser.json`.
