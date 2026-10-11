# Tools

A **tool** is a function the model can call during a turn. Tool calls are decided by the model, executed by this program, and the results are sent back to the model. How a call is executed (concurrency, timeout, errors) is in [`agent-loop.md`](agent-loop.md). Storage shapes are in [`context-and-caching.md`](context-and-caching.md).

A tool has three parts that must stay in sync by hand (there is no registry yet; a generated one is planned in #15):

1. **Schema**: an entry in `config/tools.json` (sent to the model).
2. **Dispatch**: a `case` in `tool_manager.get_tool_response` (`src/tool_manager.py`).
3. **Implementation**: a function, usually in `src/tools/`.

Also mention the tool in the base system prompt (`config/personality/base.yaml`, section `# Tools`) so the model knows when to use it. The system prompt text is hand-written; it is not generated from the schema.

## Current tools

All schemas set `"strict": true` and `"additionalProperties": false`, and list **every** property in `required` (including `get_time`, which has `"required": []`). Strict mode requires this. Keep it when adding tools.

| Tool | Parameters | Returns | Needs |
|---|---|---|---|
| `save_to_memory` | `user_info: string` (a complete sentence) | `{"state": "Information successfully saved.", "memory": "<the text>"}` | nothing |
| `get_time` | none | `{"time": "YYYY-MM-DD HH:MM:SS.ffffff", "day": "Sunday"}` in **UTC+8** (hardcoded) | nothing |
| `web_search` | `search_term: string`, `idle_message: string` | list of `[title, url, snippet]` lists; `["Unable to get results"]` on any failure | `GOOGLE_API_KEY`, `GOOGLE_SEARCH_ENGINE_ID` |
| `get_url_text` | `url: string`, `idle_message: string` | `{"text": str, "links": [{"url", "alias"}]}`; `{"state": "Unable to fetch website"}` on any failure | network |
| `set_reminder` | `description: string`, `year, month, day, hour, minute, second` (all `number`) | `{"state": "Reminder successfully created", "description", "when"}` or `{"state": "Failed to create reminder due to invalid parameters."}` | nothing |

### Behaviour of each

**`save_to_memory`** calls `Instance.add_memory`, which appends to the chat's memory list and rewrites `data/memory.json`. The text comes from the model and is stored as given. See [`context-and-caching.md`](context-and-caching.md) for how memory is shown back. Memory is per chat, not per user.

**`get_time`** returns the current time shifted by a fixed `+8` hours (`tools/time_manager.zone_offset`, "Singapore"). There is no per-user or per-chat timezone. The time string includes microseconds (`str(datetime)`). Reminders use the same offset.

**`web_search`** first sends the model's `idle_message` to the chat (a visible message like "Searching..."), then calls the Google Custom Search JSON API and returns `[title, link, snippet]` for each result. **If the Google keys are missing, the request fails and the tool returns `["Unable to get results"]` as if the search had no results** (verified). The keys are read once at import (`os.getenv`), after `.env` loads in `main.py`. They are **not** listed in `.env.example`.

**`get_url_text`** sends `idle_message` to the chat, fetches the URL with a desktop User-Agent, and returns the page's visible text **and every link on the page**. There is no size limit and no summarising (the old `query` parameter was removed because it was ignored). **A large page produces a huge result**: Wikipedia's Python article returned 92,000 characters of text and 2,241 links, roughly 70,000 tokens (estimated as characters divided by 4; the text and link counts are measured), which is added to the context and re-sent (at cache-read price after the first time) until trimmed. There is no check that the URL is public: **the model (and so any user) can make the server fetch internal addresses** (SSRF). The only timeout is the 30 s tool timeout.

**`set_reminder`** validates the date with `datetime(...)` (rejecting impossible dates), interprets it as UTC+8, converts to a Unix timestamp, and appends `{"unix_time", "description"}` to the in-memory `reminders[chat_id]` list. **A reminder in the past is accepted** and fires on the next poll (within about a second). Reminders are lost on restart. The tool also sends a `REMINDER_CREATED` indicator, which currently does nothing (see [`telegram-client.md`](telegram-client.md)).

### How reminders fire

`main.main()` runs `TimeManager.poll(on_reminder)` once per second. For each chat, each reminder whose `unix_time` is **earlier than** now (`<`, so a reminder due this exact second fires on the next poll) is passed to `on_reminder`, then removed. `on_reminder` stores an `API: The reminder '<description>' is up. Please remind accordingly. Notify the user by tagging their username.` **user** message, runs a normal turn, and sends the reply. The reminder is removed after `on_reminder` returns; if `on_reminder` raises, the exception escapes `poll` and ends the program (see [known-issues.md](known-issues.md)).

## Unknown tool names

`tool_manager.get_tool_response` returns `None` for a name with no `case`, which is stored as the JSON `null`. With `strict` schemas and a fixed tool list this should not happen, but there is no error for it.

## The `idle_message` parameter

Some tools take `idle_message`: text the model writes for the user to read while the tool runs. `tool_manager` sends it to the chat immediately, before the work. It is shown as a normal bot message (Markdown), separate from the final reply. This is an existing convention, not an API feature. A model that writes a useless or empty `idle_message` makes the bot send a useless or empty message (Telegram rejects an empty message; both send attempts then raise, which is caught by the tool wrapper and reported to the model as an error).

## Adding a tool: checklist

1. Write the implementation in `src/tools/<name>.py`. Keep it `async` if it does I/O. Return a JSON-serialisable `dict` or `list`. Put failures in the return value (`{"error": ...}` or `{"state": ...}`) where you can; unexpected exceptions are also caught by the loop and sent to the model as `{"error": "<Type>: <msg>"}`.
2. Add a `case` in `tool_manager.get_tool_response`.
3. Add the schema to `config/tools.json`: `"strict": true`, `"additionalProperties": false`, every property listed in `required`. Write the description for the **model** (when to call it, what it returns).
4. Add a numbered entry under `# Tools` in `config/personality/base.yaml`.
5. **Appending a tool changes the cached prefix once** (every chat's next request is a cache write). It is not a per-request cost, so it is fine to do at deploy time. Never add or remove tools *while the program runs*.
6. Add tests in `tests/` with the LLM and network faked (see [`contributing.md`](contributing.md)).
7. Do not make it slower than `tool_timeout_s` (30 s) without raising that value in `config/config.yaml`.

## Retired tools

`send_gif` (Tenor) and `generate_image` (DALL-E) were removed with the games features. Their code is in `unused/` and is not wired up. `client.send_gif` and `client.send_image` still exist and are unused.
