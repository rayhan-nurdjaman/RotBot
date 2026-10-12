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
| The new reminder tools work with a real model (Haiku): `set_reminder` with an ISO `when` computed from "in 15 seconds" (it rounded to the next minute, since the stamp has no seconds), `list_reminders`, `cancel_reminder` in the same turn as a set, and a reminder that came due fired once through the scheduler and `on_reminder`, marked `done` in a real SQLite file. Telegram was faked. Cache reads were 94-98% of the prompt on almost every request, with one exception: the request right after a `set_reminder` tool round in the third turn had `cached_tokens` 0, and its prompt was 3278 tokens against 3481 on the request before it. Not investigated; a different provider route is one possibility (*inferred*) | Live turns on Haiku, about $0.005 |
| The top-level `cache_control` shortcut gave **0** cached tokens with a trailing volatile message | Three consecutive requests |
| The memory tools work with a real model (Haiku), backed by a real SQLite file, Telegram faked: from one message it saved a preference **and** a standing instruction ("end replies with the word cheers") as two memories and followed the instruction from then on; a fresh `Instance` on the same database (a simulated restart) listed both memories; "forget the tea preference" deleted exactly that one; a different chat saw none. Cache reads on the later requests of a turn, and on the next turn when no memory had changed, were 93-98% of the prompt (for example 3812 of 4115 and 3996 of 4070 tokens). The first request after a memory was saved or forgotten read 0 from the cache, as designed (one cache write per change), and so did the first request of a fresh `Instance` or chat (its history differs from the cached one); the request after that hit again. Not investigated: whether the 93% on the first tool loop is the usual figure for a first hit | A six-turn script on Haiku, about $0.01 |
| A breakpoint works on a tool message (string content converted to a part) and on an image part | Two requests each, second one hit |
| A history that starts with an assistant message is accepted | One request |
| `tool_choice: "none"` with tools declared is accepted on all three models | One request each |
| A request ending with an **assistant** message is rejected (HTTP 400 "does not support assistant message prefill") | Direct request, and by the two-message race |
| An invalid model slug returns HTTP 400 and the user gets `REQUEST_MESSAGE` with their message kept | Real request |
| `max_tokens` too small on Sonnet at max effort gives `finish_reason: length` with empty content, mapped to `LENGTH_MESSAGE` | Real request with `max_tokens=40` |
| One `llm_call` log line is produced per call with real values | Real request |
| Approval buttons for `bash` (#43), real model, Telegram faked: a step with `bash` suspends with nothing run and the lock free; approve ran it and the model reported the output; deny and a 2 s expiry were told to the model, which did not retry; a message typed while waiting was held with one notice and answered after approval; a step with `bash` plus `list_reminders` ran neither before the press; after Approve for the day the next command ran with no request; cache reads 97 to 99 percent through 16 requests. Haiku passed `"null"` text for optional arguments, so one command was asked about three times. **Real Telegram not tried** | One script, about 0.3 cent, [`approvals.md`](approvals.md) |
| Auto routing (Jev): `router.route` on six hand-written conversations chose Haiku for chit-chat, a reminder and a follow-up, and Sonnet for a whiteboard question, a debugging request and a planning request; each call cost about $0.00002 and took 0.5 to 1.3 s. Telegram was not involved. Details and probabilities in [`auto-routing.md`](auto-routing.md) | Real Jev calls, about $0.0001 |
| Switching models does not evict the prompt cache: a 9-request sequence over Haiku and Sonnet (and two efforts) wrote the cache once per model and read it on every later request, including after switching back. **Without tools or server tools**; the real request shape is untested | Real requests, about $0.05, see [`auto-routing.md`](auto-routing.md) |
| Usage rows and the daily cap (#14, #31): with the cap set to $0.0005, a real Haiku turn answered and stored one row with OpenRouter's reported cost ($0.00074), the next turn returned the cap notice with no request, another chat's total stayed 0. Telegram was not involved | One script, temp database, about 0.1 cent |
| `session_id`, `user`, `parallel_tool_calls`, `max_tokens` are accepted | Every request |
| The cache is lost by a trailing message only when `web_search` has `engine: exa`; other parameters of `web_search`, `web_fetch` and function tools are fine; Haiku and Sonnet behave alike (Opus not tested) | 15 three-turn scenarios, full table in [`caching-and-server-tools.md`](caching-and-server-tools.md), about 20 cents |
| `web_search` with `engine: exa` and caps gave 2,620 prompt tokens for a search turn, without the engine about 13,400 | One request each, Haiku |
| A request-level `models: [...]` list does not rescue an invalid primary slug (400 "not a valid model ID"); its behaviour for provider errors is **not tested** | Four direct requests |
| The key's limit, usage, and the public `/models` list (prices, reasoning options) | `GET /api/v1/key` and `GET /api/v1/models` |

## Verified by running local code (not the network)

| Claim | Evidence |
|---|---|
| Config is read from the repo root only | Import from another directory raised `FileNotFoundError` |
| `OPENROUTER_API_KEY` unset makes `chat_completion` import fail; empty imports fine | Run |
| Missing `TELEGRAM_API_KEY` fails at import; a wrong token fails at startup with `InvalidToken: Unauthorized` followed by a misleading `RuntimeError` | Run |
| python-telegram-bot runs updates one at a time (`max_concurrent_updates = 1`) | Read at runtime from `client.application.update_processor` |
| Command routing: first matching handler runs; unregistered commands and `@other_bot` commands fall through to the message handler | Real `telegram.Update` objects through `check_update` |
| `send_message` (fixed, #37): Markdown rejection retries as plain text and logs the reason; empty text sends nothing; long text is split into pieces of at most 4096 characters | Stand-in bot, tests. Not real Telegram |
| A new `Instance` runs as `base`, not `rotbot`; a failed `/schizo` resets the chat to base | Run |
| The old `get_url_text` returned about 92,000 characters and 2,241 links for one Wikipedia page (tool since removed, see #16) | Run against live websites |
| `client.on_message`: `None` usernames become `"None: "`, `from_user=None` and a reply to a sender-less message raise | Run with fake updates |

## Covered by tests (Test)

394 tests, see [`contributing.md`](contributing.md). They prove the logic given fakes: grouping and ordering of tool rounds, the step cap, every finish reason branch, each error mapping, tool timeouts, malformed arguments, the lock, trimming never leaving an orphan, chunked trimming, request ordering and breakpoint placement, no `temperature`, the typing task lifecycle, reasoning kept and dropped, the memory store and its limits, the reminder scheduler (ordering, wake-up, retry, restart, cancel) and its tools, command behaviour, the `llm_call` fields.

**Tests cannot prove** that Telegram, OpenRouter or the provider cache behave as assumed. That is what the Live list is for.

## Not verified

| Item | Why it matters |
|---|---|
| **Anything on real Telegram.** The bot has never been started against a real token in this work. Photo download, the typing indicator, Markdown sending, command delivery, group privacy mode, long polling | The whole client is tested only with fakes |
| **The approval buttons on real Telegram** (#43). Each of these has to be tried once by the owner, in a chat listed in `OP_CHAT_IDS`: (1) ask for a harmless command, check the message and its three buttons look right on the phone; (2) press **Approve this time**, check the command ran, the reply came and the buttons vanished; (3) press **Deny**; (4) wait 60 s without pressing; (5) press **Approve for the day**, then ask for another command and check it runs with no buttons; (6) press a button twice quickly; (7) type a message while buttons are showing; (8) set a reminder for a minute ahead, ask for a command, and let the reminder fire while the buttons are showing; (9) ask with a very long command and check the message is cut but the command ran whole; (10) press from a second allowed account and from an unlisted one (the unlisted one must be ignored) | Whether Telegram delivers the press to the bot, whether editing the message removes the buttons, and the layout were read from the library and Telegram's documentation, never observed |
| The SDK's automatic **retries** (429, 5xx, timeouts) against a real failing server | The error mapping is tested, the retry path is not |
| Behaviour at 429 / rate limits and a 402 (insufficient credits) | Mapped by tests, never triggered |
| **Cache lifetime** (how long an ephemeral cache entry lives) and the **512-token minimum** quoted in the issue | Not measured; our prompt is about 1,900 tokens |
| Quality of Sonnet or Opus answers at different efforts | Only checked that they answer and call tools |
| Images sent to the model end to end (a real photo from Telegram) | A breakpoint on an image part was checked with a generated 64 by 64 JPEG; the encode path was not run on a real photo |
| Behaviour of `reasoning.exclude`, `reasoning.max_tokens`, the `models` fallback list, `provider` options, the OpenRouter `advisor` and `subagent` server tools | Not used |
| Large conversations (hundreds of messages), long-running stability, memory growth | Not exercised |
| `/memory` and `/forget` through Telegram (the handlers are three lines each; the logic they call is tested with a faked `send_message`) | Not exercised, no bot token |
| Whether the secrets filter catches real-world secrets, and how often it rejects harmless text | Only the listed examples are tested |
| Windows or macOS | Developed and run on Linux only; the code base used CRLF and was converted to LF |

## Dates of facts that can rot

Prices, model slugs, supported efforts, and OpenRouter's documented behaviour were read on **2026-10-11**. They can change. Re-check `https://openrouter.ai/api/v1/models` before relying on prices, and re-run the live caching check after any change to request assembly.
