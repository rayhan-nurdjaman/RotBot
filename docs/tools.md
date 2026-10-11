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

All schemas are strict (`"strict": true`, `"additionalProperties": false`, every property in `required`; a tool with no arguments has `"required": []`). Keep it that way when adding tools.

| Tool | Parameters | Returns (inside `ok: true`) | Needs |
|---|---|---|---|
| `save_to_memory` | `user_info: string` (a complete sentence) | `{"state": "Information successfully saved.", "memory": "<the text>"}` | nothing |
| `set_reminder` | `description: string`, `when: string` (ISO 8601) | `{"id", "description", "when"}` (`when` echoed in the configured zone, e.g. `2026-10-12T09:30+08:00`); `ok: false` for text that is not ISO 8601 or a time not in the future | the chat's reminders |
| `list_reminders` | none | `{"reminders": [{"id", "description", "when"}, ...]}`, this chat's waiting reminders, soonest first | the chat's reminders |
| `cancel_reminder` | `id: integer` | `{"cancelled": <id>}`; `ok: false` if the chat has no waiting reminder with that id | the chat's reminders |

Plus two **server tools** run by OpenRouter (next section): `web_search` and `web_fetch`.

### Behaviour of each

**`save_to_memory`** calls `Instance.add_memory`, which appends to the chat's memory list and rewrites `data/memory.json`. The text comes from the model and is stored as given. See [`context-and-caching.md`](context-and-caching.md) for how memory is shown back. Memory is per chat, not per user.

There is no `get_time` tool: the model reads the time from the timestamp on each user message (see [`caching-and-server-tools.md`](caching-and-server-tools.md)). The timezone is one global setting, `timezone` in `config/config.yaml` (default `Asia/Singapore`), read by `src/clock.py`.

**`set_reminder`** parses `when` with `datetime.fromisoformat` (so `Z` and offsets like `+08:00` work). A string **without an offset is read in the configured timezone** (`clock.zone`); one with an offset keeps it. The moment is stored as Unix seconds in UTC, so DST and a later change of the `timezone` setting do not move it. A time that is not strictly in the future is rejected, and the error includes the current time so the model can retry. The reminder is stored with `chat_id`, the description, and `created_by` NULL (see [known-issues.md](known-issues.md)). **`list_reminders`** and **`cancel_reminder`** only ever see the calling chat's reminders; the id of another chat's reminder is reported as not found. Reminder logic is in `src/reminders.py`, the tools in `src/tools/native.py` are thin wrappers.

## Server tools (`web_search`, `web_fetch`)

Declared in `src/tools/native.py` with `registry.server_tool('openrouter:...', description, **parameters)`. They are added to the `tools` list after the function tools (`registry.schemas()`), and **OpenRouter runs them inside the same request**: the model calls them, OpenRouter executes them, and the model continues, all before we get a response. There is no handler in this program, and the call is invisible to the loop (`finish_reason` is `stop`, `tool_calls` is empty, and the `llm_call` log line shows no tool name).

| Tool | Configured as | Cost | Notes |
|---|---|---|---|
| `web_search` | `engine: exa`, `max_results: 3`, `max_characters: 1500`, `max_uses: 3` | about $0.007 per search (Exa) plus tokens | Results come back as `message.annotations` (`url_citation`) and as the model's cited text. |
| `web_fetch` | `max_uses: 3`, `max_content_tokens: 10000` | tokens, plus at most $0.001 per fetch depending on the engine OpenRouter picks | Runs on OpenRouter's side, so the bot's host never requests the URL (no SSRF, no page-size blowup). |

What to know:

- **Billing**: to the same OpenRouter key and visible in `llm_call.cost` (a search turn costs about $0.008 on Haiku, a fetch turn under $0.001). The caps (`max_uses`) bound one message. `usage.server_tool_use_details` in the raw response reports `web_search_requests` and `tool_calls_executed`; the program does not log it yet.
- **Nothing stored**: the search or page text is **not** part of the history we keep. Only the final reply is. A follow-up question about a detail of the page cannot be answered from history, and the model may need to fetch again.
- **The model can lose track of having used them.** On later turns it sees its own cited reply but no tool call, and when asked it may claim it never searched (reproduced live; a prompt hint did not fix it). It is a wording problem only. See [`known-issues.md`](known-issues.md).
- **Mixing with function tools in one turn**: a function-tool call made after a server tool in the same turn sees no search in the replayed history, which can confuse the reply (spike on #16).
- **Privacy**: queries and URLs go to OpenRouter and its backends (Exa for search). Nothing about this is configurable per chat.
- **Not verified**: that the `max_uses` caps are enforced, and image generation (`openrouter:image_generation`) was deliberately not added.

### How reminders fire

`src/reminders.py` holds the store (the SQLite `reminders` table, created by migration 1 in `src/db.py`) and the scheduler. `main.main()` calls `db.init()`, then starts `reminders.run(main.on_reminder)` as one task, and cancels it on shutdown before `db.close()`.

**Scheduler loop (`run`).** Deliver everything due (`process_due`), then sleep until the next pending reminder is due, at most `MAX_SLEEP` (60 s, so a jumped wall clock costs at most a minute). The sleep waits on an `asyncio.Event` that `reminders.add` sets, so a reminder due sooner than the one being waited for wakes it at once. There is no per-second polling. If anything inside an iteration raises, it is logged and the loop retries after `CRASH_RETRY` (30 s); only cancellation ends it (this was issue #33).

**Delivery.** `process_due` selects `status = 'pending' AND attempt_at <= now`, oldest first, and calls `deliver(reminder, late_seconds)` for each one, one at a time. Before each call it re-checks the status, so a reminder cancelled while an earlier one was being delivered is skipped.
- **Success:** status becomes `done`. It is marked done only **after** `deliver` returns, which makes delivery **at-least-once**: a crash between sending and marking done delivers it again after restart.
- **Failure (any exception):** `attempts` goes up and `attempt_at` moves back by `RETRY_DELAYS` (30 s, 2 min, 10 min, 30 min). After `MAX_ATTEMPTS` (5) failures the status becomes `failed` and a CRITICAL line is logged. One failing reminder never blocks the others.
- **Statuses:** `pending` (waiting or retrying), `done`, `failed`, `cancelled`.
- **Restart:** the table is the only source of truth, so reminders due while the bot was down are delivered on the first pass after startup, with `late_seconds` set.

**`main.on_reminder(reminder, late_seconds)`** is the `deliver` callback. It runs a normal turn so the reminder is worded in the persona: `Assistant.get_response(instance, message="API: The reminder '<description>' is up. Please remind accordingly. Notify the user by tagging their username.", raise_errors=True)`, then sends the reply to the chat. If `late_seconds` is 120 or more, the message also says how late it is and asks the model to mention that. The `API:` message is stored under the chat lock (see [`agent-loop.md`](agent-loop.md)). If the LLM call fails (`openai.APIError`), it sends `Reminder: <description>` as plain text instead. If sending to Telegram fails, the exception propagates, and the scheduler retries later.

**Known limits.** Chat ids are stored as given (`INTEGER NOT NULL`; SQLite keeps an integer-looking value as an integer). Reminder messages stay in the conversation history like any other, and a reminder is only delivered once the chat's instance exists in memory (it is created on demand with an empty history, so a restarted bot words the reminder without the earlier conversation). `usage.server_tool_use_details` is not logged.

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
