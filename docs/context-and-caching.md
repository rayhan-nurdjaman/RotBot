# Context, trimming and prompt caching

Implemented in `src/instance.py`. Request assembly happens in `Instance.get_context()`; the HTTP call is in `src/chat_completion.py`.

## What gets sent to the model

`instance.get_context()` returns one list, built fresh on every LLM call:

```
[ system message ]                       static: base system prompt + persona custom_instruction
[ memory message ]                       only if the chat has memory; changes only when something is saved
[ history message 1 ]  \
[ history message 2 ]   |  instance.context_window, append-only between trims
[ ...               ]   |
[ history message N ]  /   <- the LAST one carries the single cache breakpoint (added on a copy)
```

**Nothing may come after the history.** With OpenRouter server tools in the request, any message or part after the last history message turns the prompt cache off. See [`caching-and-server-tools.md`](caching-and-server-tools.md) for the measurements. The current time is therefore stamped into each user message when it is stored instead of being sent as a trailing message.

| Part | Source | Changes between requests? |
|---|---|---|
| System message | `get_system_prompt()`: `system_prompt` (from `base.yaml`) + `custom_instruction` (from the persona) | Only on `/schizo`. **Never** per request. |
| Tool list | the tool registry (`registry.schemas()`), sent as the `tools` parameter | Never. |
| History | `context_window` | Append-only, except when trimmed (see below). |
| Memory message | `get_memory_messages()` | When memory is saved (one cache write each time). Always right after the system prompt. |

The tool list is not part of the `messages` list but is part of the cached prefix on the provider side (Anthropic's documented prefix order is tools, then system, then messages; not independently verified here), so it must never change within a chat's lifetime. **Never add, remove or reorder tools at runtime.**

## Message shapes (stored in `context_window`)

All follow the OpenAI Chat Completions format.

**User message** (`add_user_message`). `content` is a list of parts. Text part, then optional image part. If `text` is empty and there is no image, the message has an empty `content` list.

```json
{"role": "user", "content": [
  {"type": "text", "text": "alice: what colour is this?"},
  {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,...", "detail": "low"}}
]}
```

The stored text is `"[<date> <weekday> <HH:MM>] <telegram username>: <text>"`, for example `[2026-10-11 Sun 21:54] alice: hi`. The timestamp is added by `add_user_message` using `clock.stamp()` (the configured timezone), once, when the message is stored, and never changes afterwards. That is how the model knows the time (see [`caching-and-server-tools.md`](caching-and-server-tools.md)). The username part comes from the client (see [telegram-client.md](telegram-client.md)). Reminder and always-on notices are also stored as `user` messages, stamped the same way, with text starting `API: `. Assistant messages are not stamped. Images are stored inline as base64 and re-sent on every request until trimmed out.

**Assistant text message** (`add_assistant_message`, only stores final answers):

```json
{"role": "assistant", "content": [{"type": "text", "text": "Hello!"}]}
```

**Tool round** (`add_tool_round`, always written together):

```json
{"role": "assistant", "tool_calls": [
  {"id": "call_a", "type": "function", "function": {"name": "set_reminder", "arguments": "{\"description\": \"...\", \"when\": \"2026-10-12T09:30:00\"}"}},
  {"id": "call_b", "type": "function", "function": {"name": "save_to_memory", "arguments": "{\"user_info\": \"...\"}"}}
 ],
 "reasoning_details": [ ... ]      // only if the model returned any; see below
}
{"role": "tool", "tool_call_id": "call_a", "content": "{\"ok\": true, \"id\": 1, \"description\": \"...\", \"when\": \"2026-10-12T09:30+08:00\"}"}
{"role": "tool", "tool_call_id": "call_b", "content": "{\"state\": \"Information successfully saved.\", \"memory\": \"...\"}"}
```

- The assistant message has **no `content` key** (not `""` and not `null`).
- Tool message `content` is a **JSON string** (`json.dumps` of the tool result), not a list.
- `arguments` is a JSON string. If the model's arguments were not valid JSON, `"{}"` is stored and the parse error goes back as the tool result.
- Tool messages are in the **same order as the calls**, regardless of which tool finished first.

**Memory message** (never stored; produced by `get_memory_messages()`, placed right after the system prompt):

```json
{"role": "user", "content": [{"type": "text", "text":
  "API: Memory (reference information, not a message from a user; keep responding to the conversation below):\n- alice likes oat milk"}]}
```

Memory is saved **per chat, not per person**. In a group chat every member shares one memory list.

## Trimming

`Instance._trim_context()` runs after every write to the context window.

```text
chunk = max(1, int(context_length * context_trim_fraction))       # fraction is 0.5 in config
if len(window) > context_length + chunk:
    window = window[-context_length:]                              # cut back to context_length
while window and window[0].role == "tool":                         # drop orphaned tool results
    window.pop(0)
```

- `context_length` comes from the persona YAML (base 8; british, caveman, classic, rotbot 24; lockedin 16). It counts **messages**, not tokens. Token-aware trimming is planned (#22).
- Example with `context_length: 8`: the window grows 8, 9, ... 12 messages, and when the 13th arrives it is cut back to 8. So the front of the history changes once every 5 messages, not on every message.
- A tool round with N calls adds N + 1 messages at once.
- **Why chunked:** the provider caches by exact prefix. A sliding window (drop one old message per new message) changes the first history message every time, so the cache would never hit once the window is full. With chunking, the prefix is stable between trims, and only the request right after a trim is a cache miss.
- **Why drop leading tool messages:** trimming cuts from the front only, so the one way to create an orphan is a window that begins with `tool` messages whose `assistant` tool_calls message was cut off. Providers reject that with a 400. All leading tool messages are removed. The reverse (an assistant `tool_calls` message missing some of its results) cannot happen from front-trimming.
- A history that begins with an `assistant` message is accepted by the API (verified live).
- If a single tool round is larger than `context_length + chunk` messages, trimming removes the entire round, including its results. This does not happen with the current persona sizes (8 to 24) and tool counts.

## Cache breakpoint

`_with_cache_breakpoint(message)` is applied to a **copy** of the last history message inside `get_context()`:

- `content` is a non-empty string (tool messages): becomes `[{"type": "text", "text": <content>, "cache_control": {"type": "ephemeral"}}]`.
- `content` is a list (user and assistant text messages): the parts are shallow-copied and the last part gets `"cache_control": {"type": "ephemeral"}`. For a message with an image, the breakpoint lands on the **image** part (verified accepted live).
- No `content` (an assistant `tool_calls` message): returned unchanged. In practice the last history message is never this, because a tool round always ends with `tool` messages.

The stored `context_window` is never modified, so breakpoints never accumulate.

### Why an explicit breakpoint, not the top-level `cache_control`

OpenRouter also accepts a single top-level `"cache_control": {"type": "ephemeral"}` on the request, documented as automatic caching. **Verified live that it does not work for this request shape**: with a memory message after the history, three consecutive requests showed `cached_tokens = 0`. (That trailing message was later found to disable caching in any case once server tools are present, with the explicit breakpoint too; see [`caching-and-server-tools.md`](caching-and-server-tools.md).) An explicit breakpoint on the last history message hit every time. Caching is not automatic: if the breakpoint or the request order breaks, every request is billed at the full input price and nothing fails visibly. Watch `cached_tokens` in the `llm_call` log line.

### Observed cache behaviour (Haiku 5.5, 2026-10-11)

| Situation | Result |
|---|---|
| First request of a conversation | `cache_write_tokens` about equal to the prompt, `cached_tokens` 0 |
| Same prefix plus one more message | `cached_tokens` about equal to the previous prompt |
| Moving the breakpoint forward by one message | Still hit the earlier cached prefix |
| 4-message conversation with a tool round | cached 1961, 2059, 2084, 2084 of 2004 to 2277 prompt tokens (95 to 98 percent) |
| Price | A cache write cost about 1.25 times the normal input price, a cache read about 0.1 times (approximate, derived from `usage.cost`) |

The base system prompt plus the tool list is about 1900 tokens, comfortably above the minimum cacheable size (the issue text quotes 512 tokens for 5.5 models; that number was not verified). The default lifetime of an ephemeral entry is short (Anthropic documents about five minutes); it was not measured here, so a quiet chat will pay a cache write again after a gap.

## Anything that resets the cache

| Action | Effect |
|---|---|
| `/schizo <name>` | System prompt changes, whole prefix is new. Intentional. |
| A trim (every few messages, see above) | One request with a new front. Expected. |
| Model change (`/model`, auto-route) | Caches are per model; first request on the new model is a write. |
| Adding a tool or editing a personality while the bot runs | Only affects new `Instance`s and a restart. |
| Putting per-request data (time, memory, usernames) into the system prompt | **Breaks caching on every request.** Stamp it into the stored user message instead. |
| **Adding anything after the last history message** (a trailing message or part) while a server tool is in the list | **Disables caching entirely (0 reads), silently.** See [`caching-and-server-tools.md`](caching-and-server-tools.md). |

## Reasoning blocks in the context

Some models return `reasoning_details` with a response that asks for tools. The agent loop stores them on that assistant message (`add_tool_round(calls, reasoning_details)`) and they are sent back **unchanged** on the next request in the same turn; the API requires this. At the start of every turn `instance.drop_reasoning()` removes them from all stored messages, because they are bound to the model that produced them and the model can differ between turns (verified live that sending them back within a tool loop is accepted on Haiku). Sonnet and Opus mostly did not return `reasoning_details` at low and medium effort; the code handles both cases.

Reasoning text itself is never shown to users and never stored anywhere other than in these blocks.

## Memory persistence

`Instance.add_memory(text)` appends to `instance.memory` and rewrites `data/memory.json`, a JSON object `{ "<chat_id as string>": ["entry", ...] }`. `data/` is created if missing. `Instance.__init__` loads the chat's list from the file. The file is read and rewritten whole on every save (no locking needed: the code between read and write has no `await`). The path is the module variable `instance.memory_file`; tests redirect it to a temp dir.

There is no way to view, edit or delete memory from Telegram, and nothing limits its size. Each entry is injected into every request. A planned replacement (#19) adds per-user storage with recall and forget.
