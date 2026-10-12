# Prompt caching and OpenRouter server tools

**Read this before changing how `get_context()` builds a request, or before adding or removing a server tool** (`openrouter:web_search`, `openrouter:web_fetch`, anything with a type starting `openrouter:`).

Measured on 2026-10-11 against the live OpenRouter API with `anthropic/claude-haiku-5.5` (`reasoning.effort: low`). Everything below is an observation. The **trigger is known (see the follow-up) but the cause is not**, and OpenRouter may change this behaviour at any time. If you can re-run the recipe at the end, do.

## The rule

> **While `web_search` is configured with `engine: exa` (as it is now), the request must not contain anything after the history.** (The first measurements blamed server tools in general; the 2026-10-12 follow-up below narrowed it to that parameter.) The last message of the request must be the last message of the stored history, and that message carries the one cache breakpoint. Per-request data (current time, memory) must not be added as a trailing message or as an extra part of the last message.

If you break this rule nothing fails. The bot works, replies look normal, and **every request becomes a full cache write: 0 cache reads**. Cost rises, silently. The only symptom is `cached_tokens: 0` in the `llm_call` log lines (and `cache_write_tokens` roughly equal to `prompt_tokens`).

## Why it matters

A cache read costs about 0.1 times the normal input price and a cache write about 1.25 times. With the current prompt (about 2,600 tokens before any history) a Haiku turn costs about $0.0002 cached and $0.0005 uncached. History grows this, and Sonnet and Opus input prices are roughly 5 and 15 times Haiku's, so the loss grows with both.

## How the problem appeared

The first design put per-request data in a **trailing volatile message** after the history (`get_volatile_messages()`): the stored memory, and then the current time. That was the right design for function tools, and it was measured at 95 to 98 percent cache reads in Milestone 1 (#13). It stopped working once the server tools were added in #16, and the cache check done for #16 did not catch it because that test chat had no memory, so it had no trailing message. Found while adding the current time (#20); tracked in #41.

## Measurements

All cases: padded system prompt (about 3,500 tokens with function tools only, about 4,500 with the server tools in the list), a unique first line so earlier cache entries cannot be hit, `session_id` set the same on each request, three consecutive turns. Each pair is `(prompt_tokens, cached_tokens)` or listed as cache reads on turns 2 and 3.

| # | Tool list | After the history | Cache reads, turns 2 and 3 |
|---|---|---|---|
| 1 | function tools only | trailing user message (changing text) | **3,547 and 3,589: works** |
| 2 | function tools + `web_search` + `web_fetch` | nothing | **4,520 and 4,565: works** |
| 3 | function tools + `web_search` + `web_fetch` | trailing user message with changing text | **0 and 0** |
| 4 | same as 3 | trailing user message with fixed text | 0 and 0 |
| 5 | same as 3 | short fixed trailing message | 0 and 0 |
| 6 | same as 3, plus a second breakpoint on the system prompt | trailing user message | 0 and 0 |
| 7 | same as 3, breakpoint on the system prompt only | trailing user message | 0 and 0 |
| 8 | same as 3 | the volatile text added as an extra part of the last user message | 0 and 0 |
| 9 | same as 3, memory present, code as of commit `09c33ff` | trailing memory message | 0, 0, 0 (three turns) |

What this shows:

- Server tools alone (case 2) and a trailing message alone (case 1) are both fine. **The combination is what breaks.**
- The text of the trailing message does not matter (cases 3, 4 and 5), nor where the breakpoints are (6 and 7), nor whether it is a separate message or a part of the last one (8).
- Case 9 shows this was already hurting the pushed code for any chat with saved memory, from commit `09c33ff` until it was fixed by this change.
- Narrowed down on 2026-10-12, see [the follow-up](#follow-up-which-parameter-triggers-it-2026-10-12) below. The trigger is the `engine: exa` parameter of `web_search`, not the server tools in general. Why it breaks the cache is still unknown.

## Follow-up: which parameter triggers it (2026-10-12)

Same method as above (padded prompt of about 10,000 tokens, `session_id` fixed, three consecutive turns, a trailing volatile message on every request unless noted, one cache breakpoint on the newest history message). Each cell is `prompt_tokens / cached_tokens` for turns 1, 2 and 3. About 20 cents in total.

| Tool list | Model | Cache reads, turns 2 and 3 |
|---|---|---|
| function tools + `web_search` (our parameters) + `web_fetch`, **no** trailing message | Haiku | 11080 and 11089: works |
| same list, trailing message | Haiku | **0 and 0** |
| function tools + `web_search` (our parameters), trailing message | Haiku | **0 and 0** |
| function tools + `web_fetch` only, trailing message | Haiku | 10860 and 10869: works |
| function tools only, trailing message | Haiku | 10099 and 10108: works |
| function tools + `web_search` with **no parameters**, trailing message | Haiku | 12629 and 12638: works |
| `web_search` with no parameters, alone, trailing message | Haiku | 11122 and 11131: works |
| `web_search` with our parameters, alone, trailing message | Haiku | **0 and 0** |
| `web_search` with only `engine: exa`, trailing message | Haiku | **0 and 0** |
| `web_search` with only `max_results: 3`, trailing message | Haiku | 11119 and 11128: works |
| `web_search` with only `max_characters: 1500`, trailing message | Haiku | 11121 and 11130: works |
| `web_search` with only `max_uses: 3`, trailing message | Haiku | 11124 and 11133: works |
| function tools + `web_search` + `web_fetch`, no trailing message | Sonnet | 11081 and 11090: works |
| same list, trailing message | Sonnet | **0 and 0** |
| function tools + `web_search` with no parameters, trailing message | Sonnet | 11112 and 11121: works |

What this shows:

- **The trigger is `engine: exa`.** Every other parameter of `web_search`, and `web_fetch`, can sit next to a trailing message without losing the cache. It is not "server tools in general", as this page first assumed.
- **Sonnet behaves like Haiku** (Opus was not tested).
- The cause is still unknown. It is plausible that OpenRouter handles a request with the Exa engine differently (inferred, nothing in the responses says so).

Why the code still sends `engine: exa` (decision: keep it, the owner has not asked to change it). Same question, one request, Haiku:

| `web_search` parameters | Prompt tokens | Cost |
|---|---|---|
| `engine: exa`, `max_results: 3`, `max_characters: 1500`, `max_uses: 3` (the code) | 2,620 | $0.0073 |
| the same without `engine` | 13,381 | $0.0115 |
| no parameters at all | 13,992 | $0.0116 |

With Exa the size caps work and the search results stay small; with the default engine the caps seem not to apply (inferred from the token counts) and a search turn carries about 11,000 more prompt tokens, which also sit in the history afterwards. One request each, so this is an indication, not a benchmark. **If the engine is ever dropped** the trailing-message design (current time and memory after the history, no stamps) becomes possible again, at the price above. Re-run the recipe first.

## The design that works (since the #20 work)

Request order in `Instance.get_context()`:

```
[ system message ]            static: prompt, tools section, timezone name
[ memory message ]            only if the chat has memory; changes only when something is saved
[ history message 1..N ]      append-only between trims; the LAST one carries the cache breakpoint
```

- **Current time**: stamped into the text of every user message when it is stored (`[2026-10-11 Sun 21:54] alice: hi`, see `clock.stamp()` and `Instance.add_user_message`). Stored text never changes afterwards, so the history stays append-only and cacheable. The model reads the time off the latest message. The system prompt names the timezone once (`{timezone}` placeholder, filled from `config.yaml: timezone`). Messages that are not typed by a user (`API: ...` notices) are stamped too, since they go through the same method.
- **Memory**: moved to a message right after the system prompt instead of a trailing one. It changes only when a memory is saved or removed, so the cost is one cache write per save instead of one per turn. This is also what made case 10 recover.
- **Staleness**: the time is the time the user message was stored. Inside a long tool loop (a few seconds, up to 8 steps) it does not move. The model does not need more precision than that.
- **Side benefit**: the model can see when each earlier message was sent ("what did I say an hour ago?"), which a single "current time" line cannot give.
- **Cost of the design**: one stamp is about 10 tokens per user message. The model is told not to start its own replies with a timestamp. In the few live replies checked it did not, but that was not tested at scale.

### Verified result after the change

Same padded setup, three or four turns, Haiku:

| Situation | `(prompt_tokens, cached_tokens)` per turn |
|---|---|
| No memory | (4625, 0), (4671, 4621), (4717, 4667), (4778, 4713) |
| Memory present | (4661, 0), (4721, 4657), (4769, 4717), (4837, 4765) |
| Turn 1 is a `set_reminder` tool round (two LLM calls), turn 2 a web search | (4640, 0), (4903, 4636), (11468, 9883) |

Cache reads are about 99 percent of the prompt after the first turn, with server tools in the list and with memory present. (Not measured: a long conversation that gets trimmed, or Sonnet and Opus.)

## Rules for changes

1. Do not add a trailing message, a trailing part, or anything after the last history message, while any `openrouter:*` tool is in the list. If you must, run the recipe below first and expect it to fail.
2. Anything that changes per request belongs in the stored history, stamped when it is stored, and never edited afterwards. Anything that changes rarely (memory) can sit right after the system prompt.
3. Never put per-request data in the system prompt (it changes the whole prefix every request).
4. If you add a new server tool or change a server tool's parameters, re-run the recipe. Changing the tool list invalidates the cache once (expected). It must recover on the next request.
5. After any change to request assembly, check `cached_tokens` on a **second** request in a real chat. No unit test can see the provider cache. The unit tests only guard the order and the stability of the prefix (`tests/test_cache.py`, `tests/test_clock.py`).

## Recipe to re-check (costs about $0.01 per scenario on Haiku)

1. Load `.env`. Build an `Instance` and prepend a unique line (for example a random token) to `instance.system_prompt`, plus about 200 repetitions of a filler sentence, so the prompt is well above the minimum cacheable size and cannot hit an older cache entry.
2. Make three or four consecutive `assistant.get_response(instance)` calls, adding a short user message each time.
3. Read `cached_tokens` and `cache_write_tokens` from the `llm_call` log lines (or from the raw `usage.prompt_tokens_details`).
4. Expected: turn 1 is a write (`cached_tokens` 0). Turns 2 onward have `cached_tokens` close to the previous turn's `prompt_tokens`.
5. To test a hypothesis, call `openai_client.chat.completions.create` directly with the same messages and `tools=registry.schemas()` and vary one thing at a time (this is how the table above was made). Use the same `extra_body={'session_id': ...}` on every request.

## Related

- GitHub issue #41 ("Prompt cache is disabled by any trailing message when OpenRouter server tools are in the request"). Closed on 2026-10-12 with the trigger identified (the `engine: exa` parameter).
- [`context-and-caching.md`](context-and-caching.md): message shapes, trimming and breakpoints in general.
- [`tools.md`](tools.md): the server tools themselves.
- [`verification-status.md`](verification-status.md): what was measured.
