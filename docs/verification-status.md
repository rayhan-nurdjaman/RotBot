# Verification status

What has been proven, and how. Use this to decide how much to trust a piece of behaviour. Dates are 2026-10-11 unless noted. Live checks used a real OpenRouter key; total spend was about five cents.

## Legend

| Mark | Meaning |
|---|---|
| **Live** | Run against the real OpenRouter API and the behaviour observed |
| **Test** | Covered by a unit test with fakes (`env/bin/python -m pytest`) |
| **Read** | Confirmed by reading the code or the installed library source, not run |
| **Not verified** | Neither run nor tested |

## Verified against the real OpenRouter API (Live)

| Claim | How |
|---|---|
| The three pinned slugs exist and answer through the `openai` SDK | One request per model |
| A tool-call round trip works on Haiku, Sonnet and Opus, including **parallel** tool calls (two calls in one response) | Real turns with `get_time` plus `save_to_memory`, and with `get_time` plus `set_reminder` |
| `openrouter:web_search` (Exa, capped) and `openrouter:web_fetch` (capped) work next to our function tools through `assistant.get_response`; results arrive as cited text in the final reply; a fetch with `max_content_tokens: 4000` cost 7.4k prompt tokens against 68k without; cache reads were high (5.9k of 6.7k prompt tokens on turn 2) with server tools in the list **and no memory**. That check was incomplete: with a trailing message (memory) the cache did not work at all, see the next rows. The model's later claim about whether it searched is unreliable (see known-issues) | Real turns on Haiku, about $0.10 in total |
| Registry-generated strict schemas (the three function tools) are accepted, and a `get_time` call returned `{"ok": true, ...}` to the model on Haiku (tool since removed) | One real turn after the registry change |
| `reasoning: {"effort": ...}` is accepted; the usage object includes `reasoning_tokens`, `cached_tokens`, `cache_write_tokens`, `cost` | Real responses |
| Haiku returns `reasoning_details` on tool-call responses and the API accepts them sent back unchanged on the next request in the loop | Two consecutive tool rounds with reasoning returned and replayed |
| Sonnet and Opus mostly did **not** return `reasoning_details` at low and medium effort | Observed, not exhaustive |
| An explicit `cache_control` breakpoint on the last history message gives cache hits (95 to 98 percent of the prompt) across consecutive requests, including across a tool round, with a trailing memory message present | A 4-message conversation with a tool round |
| With any `openrouter:*` server tool in the tool list, a trailing message after the history (any text, any breakpoint placement, or as an extra part of the last message) gives **0 cache reads**; function tools alone with a trailing message, and server tools with no trailing message, both cache normally | About ten scenarios of three turns each, Haiku; full table in [`caching-and-server-tools.md`](caching-and-server-tools.md) |
| The fix (time stamped into stored user messages, memory right after the system prompt, nothing after the history) restores about 99 percent cache reads, with server tools and with memory, and across a tool round and a web search | Four-turn runs on Haiku |
| The model reads the current time off the stamp on the latest message and answers day, date and relative dates correctly; `set_reminder` arguments computed from it were correct (`+1 hour` and `+2 hours`) | Live turns on Haiku |
| The top-level `cache_control` shortcut gave **0** cached tokens with a trailing volatile message | Three consecutive requests |
| A breakpoint works on a tool message (string content converted to a part) and on an image part | Two requests each, second one hit |
| A history that starts with an assistant message is accepted | One request |
| `tool_choice: "none"` with tools declared is accepted on all three models | One request each |
| A request ending with an **assistant** message is rejected (HTTP 400 "does not support assistant message prefill") | Direct request, and by the two-message race |
| An invalid model slug returns HTTP 400 and the user gets `REQUEST_MESSAGE` with their message kept | Real request |
| `max_tokens` too small on Sonnet at max effort gives `finish_reason: length` with empty content, mapped to `LENGTH_MESSAGE` | Real request with `max_tokens=40` |
| One `llm_call` log line is produced per call with real values | Real request |
| `session_id`, `user`, `parallel_tool_calls`, `max_tokens` are accepted | Every request |
| The key's limit, usage, and the public `/models` list (prices, reasoning options) | `GET /api/v1/key` and `GET /api/v1/models` |

## Verified by running local code (not the network)

| Claim | Evidence |
|---|---|
| Config is read from the repo root only | Import from another directory raised `FileNotFoundError` |
| `OPENROUTER_API_KEY` unset makes `chat_completion` import fail; empty imports fine | Run |
| Missing `TELEGRAM_API_KEY` fails at import; a wrong token fails at startup with `InvalidToken: Unauthorized` followed by a misleading `RuntimeError` | Run |
| python-telegram-bot runs updates one at a time (`max_concurrent_updates = 1`) | Read at runtime from `client.application.update_processor` |
| Command routing: first matching handler runs; unregistered commands and `@other_bot` commands fall through to the message handler | Real `telegram.Update` objects through `check_update` |
| `send_message`: Markdown rejection retries as plain text; empty text and over-4096 text raise after the retry | Stand-in bot |
| A new `Instance` runs as `base`, not `rotbot`; a failed `/schizo` resets the chat to base | Run |
| `poll()` raises `RuntimeError: dictionary changed size during iteration` and an `on_reminder` exception escapes it | Run |
| The old `get_url_text` returned about 92,000 characters and 2,241 links for one Wikipedia page (tool since removed, see #16) | Run against live websites |
| `client.on_message`: `None` usernames become `"None: "`, `from_user=None` and a reply to a sender-less message raise | Run with fake updates |

## Covered by tests (Test)

130 tests, see [`contributing.md`](contributing.md). They prove the logic given fakes: grouping and ordering of tool rounds, the step cap, every finish reason branch, each error mapping, tool timeouts, malformed arguments, the lock, trimming never leaving an orphan, chunked trimming, request ordering and breakpoint placement, no `temperature`, the typing task lifecycle, reasoning kept and dropped, memory persistence, reminders firing once, command behaviour, the `llm_call` fields.

**Tests cannot prove** that Telegram, OpenRouter or the provider cache behave as assumed. That is what the Live list is for.

## Not verified

| Item | Why it matters |
|---|---|
| **Anything on real Telegram.** The bot has never been started against a real token in this work. Photo download, the typing indicator, Markdown sending, command delivery, group privacy mode, long polling | The whole client is tested only with fakes |
| The SDK's automatic **retries** (429, 5xx, timeouts) against a real failing server | The error mapping is tested, the retry path is not |
| Behaviour at 429 / rate limits and a 402 (insufficient credits) | Mapped by tests, never triggered |
| **Cache lifetime** (how long an ephemeral cache entry lives) and the **512-token minimum** quoted in the issue | Not measured; our prompt is about 1,900 tokens |
| Quality of Sonnet or Opus answers at different efforts | Only checked that they answer and call tools |
| Images sent to the model end to end (a real photo from Telegram) | A breakpoint on an image part was checked with a generated 64 by 64 JPEG; the encode path was not run on a real photo |
| Behaviour of `reasoning.exclude`, `reasoning.max_tokens`, the `models` fallback list, `provider` options, the OpenRouter `advisor` and `subagent` server tools | Not used |
| Large conversations (hundreds of messages), long-running stability, memory growth | Not exercised |
| Windows or macOS | Developed and run on Linux only; the code base used CRLF and was converted to LF |

## Dates of facts that can rot

Prices, model slugs, supported efforts, and OpenRouter's documented behaviour were read on **2026-10-11**. They can change. Re-check `https://openrouter.ai/api/v1/models` before relying on prices, and re-run the live caching check after any change to request assembly.
