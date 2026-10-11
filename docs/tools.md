# Tools

A **tool** is a function the model can call during a turn. Tool calls are decided by the model, executed by this program, and the results are sent back to the model. How a call is executed (concurrency, timeout, errors) is in [`agent-loop.md`](agent-loop.md). Storage shapes are in [`context-and-caching.md`](context-and-caching.md).

Tools live in a **registry** (`src/tools/registry.py`). A tool is one async function with a `@tool` decorator in `src/tools/native.py`. Its name, description, strict JSON schema and its line in the system prompt are all generated from that one function, so they cannot drift apart.

```python
@tool
async def save_to_memory(ctx, user_info: Annotated[str, Field(description='The information to store, provided as a complete sentence.')]):
	"""Stores non-sensitive user information to enhance future responses."""
	return ctx.instance.add_memory(user_info)
```

- **Name**: the function name.
- **Description**: the docstring. It is sent as the tool description **and** listed in the `# Tools` section of the system prompt (each docstring line becomes a bullet). The base prompt contains a `{tools}` placeholder that `Instance.get_system_prompt` fills from `registry.prompt_section()`. Write it for the model: when to use it, what it returns, what to combine it with.
- **Arguments**: every parameter after `ctx`. Each needs a type hint and **no default** (strict schemas require every property). Describe them with `Annotated[type, Field(description=...)]`.
- **`ctx`** is a `ToolContext` (`ctx.instance`, `ctx.chat_id`, `ctx.client`). Tools use it instead of importing `client` or `main`. `ctx.client` is the `client` module (`send_message`, `send_image`, `typing`).
- **Registration order is the order of the `tools` list**, which is part of the cached prompt prefix. It is fixed at import time.

## Results

`registry.call(name, arguments, ctx)` always returns a dict in one standard shape:

| Case | Result |
|---|---|
| Handler returned a dict | `{"ok": true, ...that dict}` |
| Handler raised `ToolError("msg")` (expected failure) | `{"ok": false, "error": "msg"}` |
| Arguments failed validation (missing, wrong type, extra) | `{"ok": false, "error": "Invalid arguments: ..."}`, the handler is not run |
| Unknown tool name | `{"ok": false, "error": "Unknown tool '<name>'."}` |
| Unexpected exception in a handler | propagates; `assistant._process_tool_call` logs it and returns `{"ok": false, "error": "<Type>: <msg>"}` |
| Timeout (`tool_timeout_s`) | `{"ok": false, "error": "The tool timed out after N seconds."}` |

Arguments are validated with Pydantic before the handler runs. Pydantic coerces compatible values (the string `"5"` becomes `5` for an `int`), and rejects unknown extra keys.

## Current tools

All schemas are strict (`"strict": true`, `"additionalProperties": false`, every property in `required`, including `get_time` with `"required": []`). Keep it that way when adding tools.

| Tool | Parameters | Returns (inside `ok: true`) | Needs |
|---|---|---|---|
| `save_to_memory` | `user_info: string` (a complete sentence) | `{"state": "Information successfully saved.", "memory": "<the text>"}` | nothing |
| `get_time` | none | `{"time": "YYYY-MM-DD HH:MM:SS.ffffff", "day": "Sunday"}` in **UTC+8** (hardcoded, one global setting) | nothing |
| `web_search` | `search_term: string` | `{"results": [[title, url, snippet], ...]}`; `ok: false` ("Unable to get results.") on any failure | `GOOGLE_API_KEY`, `GOOGLE_SEARCH_ENGINE_ID` |
| `get_url_text` | `url: string` | `{"text": str, "links": [{"url", "alias"}]}`; `ok: false` ("Unable to fetch website") on any failure | network |
| `set_reminder` | `description: string`, `year, month, day, hour, minute, second` (all `integer`) | `{"state": "Reminder successfully created", "description", "when"}`; `ok: false` on an impossible date | nothing |

### Behaviour of each

**`save_to_memory`** calls `Instance.add_memory`, which appends to the chat's memory list and rewrites `data/memory.json`. The text comes from the model and is stored as given. See [`context-and-caching.md`](context-and-caching.md) for how memory is shown back. Memory is per chat, not per user.

**`get_time`** returns the current time shifted by a fixed `+8` hours (`tools/time_manager.zone_offset`, "Singapore"). There is no per-user or per-chat timezone. The time string includes microseconds (`str(datetime)`). Reminders use the same offset.

**`web_search`** calls the Google Custom Search JSON API and returns `[title, link, snippet]` for each result. **If the Google keys are missing, the request fails and the tool returns `ok: false` with "Unable to get results."**, which the model may relay as "search is not working" (previously it looked like an empty result list). The keys are read once at import (`os.getenv`), after `.env` loads in `main.py`. They are **not** listed in `.env.example`.

**`get_url_text`** fetches the URL with a desktop User-Agent, and returns the page's visible text **and every link on the page**. There is no size limit and no summarising (the old `query` parameter was removed because it was ignored). **A large page produces a huge result**: Wikipedia's Python article returned 92,000 characters of text and 2,241 links, roughly 70,000 tokens (estimated as characters divided by 4; the text and link counts are measured), which is added to the context and re-sent (at cache-read price after the first time) until trimmed. There is no check that the URL is public: **the model (and so any user) can make the server fetch internal addresses** (SSRF). The only timeout is the 30 s tool timeout.

**`set_reminder`** validates the date with `datetime(...)` (rejecting impossible dates), interprets it as UTC+8, converts to a Unix timestamp, and appends `{"unix_time", "description"}` to the in-memory `reminders[chat_id]` list. **A reminder in the past is accepted** and fires on the next poll (within about a second). Reminders are lost on restart. The tool also sends a `REMINDER_CREATED` indicator, which currently does nothing (see [`telegram-client.md`](telegram-client.md)).

### How reminders fire

`main.main()` runs `TimeManager.poll(on_reminder)` once per second. For each chat, each reminder whose `unix_time` is **earlier than** now (`<`, so a reminder due this exact second fires on the next poll) is passed to `on_reminder`, then removed. `on_reminder` stores an `API: The reminder '<description>' is up. Please remind accordingly. Notify the user by tagging their username.` **user** message, runs a normal turn, and sends the reply. The reminder is removed after `on_reminder` returns; if `on_reminder` raises, the exception escapes `poll` and ends the program (see [known-issues.md](known-issues.md)).

## No status messages

Tools no longer take an `idle_message` and send nothing to the chat while they run. The typing indicator (see [`telegram-client.md`](telegram-client.md)) covers the whole turn.

## Adding a tool: checklist

1. Write an async function in `src/tools/native.py` (or a module it imports) and decorate it with `@tool`. First parameter `ctx`; one type-hinted, described, default-less parameter per argument; a docstring written for the model. Return a JSON-serialisable `dict`, and raise `ToolError("message")` for failures you expect.
2. That is all: the schema, dispatch and the system prompt list are generated. No `config/tools.json`, no `case`, no prompt edit.
3. **Appending a tool changes the cached prefix once** (every chat's next request is a cache write). It is not a per-request cost, so it is fine to do at deploy time. Never add or remove tools *while the program runs*.
4. Add tests in `tests/` with the LLM and network faked (see [`contributing.md`](contributing.md)). For a tool, call `registry.call(name, args, ToolContext(instance, fake_client))`.
5. Do not make it slower than `tool_timeout_s` (30 s) without raising that value in `config/config.yaml`.

## Retired tools

`send_gif` (Tenor) and `generate_image` (DALL-E) were removed with the games features. Their code is in `unused/` and is not wired up. `client.send_gif` and `client.send_image` still exist and are unused.
