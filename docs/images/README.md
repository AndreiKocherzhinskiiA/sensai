# README media

Captured on 15 November 2026 from application revision `0728fdc` in Chromium at 1440 × 960.

- `workspace.gif`: open saved sample chats, toggle the Documents panel, and change appearance through Account settings.
- `chat.png`, `chat-light.png`: dark and light chat views.
- `checklist.gif`: open a saved sample task list, mark three items complete, reload, and confirm the saved completion state.
- `structured.png`: the structured task workspace before completion updates.

The application ran in stub mode with disposable SQLite databases. Conversations were seeded through `SessionStore`; the task list was schema-validated and seeded through `ResultStore`. No private chat history, uploaded documents, credentials, or team databases were used. Navigation, theme controls, checklist writes, and reloads used the application UI and API. No inference, web search, document indexing, or extraction was recorded.

To refresh the media, start an isolated app instance, create non-sensitive sample content, repeat these interactions in a 1440 × 960 browser, and record screenshots at five frames per second. Encode GIFs at 1100 pixels wide with a shared palette. Keep the disclosure in the main README when using seeded content.
