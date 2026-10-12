# The agent loop

Implemented in `src/assistant.py`. Entry point: `assistant.get_response(instance) -> str`. It always returns a string to send to the chat (a model answer or a short error message) and only raises for bugs, not for API failures.

## Algorithm

```text
get_response(instance, message=None, raise_errors=False):   # the CALLER holds instance.lock
    if message:
        instance.add_user_message(message)            # e.g. a reminder's "API: ..." text
    try:
        return await _run_turn(instance)
    except openai.APIError as e:
        if raise_errors: raise                        # for callers with their own fallback (reminders)
        return _error_message(e)                      # nothing stored; user's message stays

_run_turn(instance):
    instance.drop_reasoning()                         # reasoning blocks never cross turns
    model = await router.route(instance) if instance.auto_route else instance.model   # once per turn
    repeat max_tool_steps times:                      # 8
        response = chat_completion.create(instance.get_context(), model, tools, chat_id)
        if response.finish_reason != "tool_calls" or no tool_calls on the message:
            return _finish(instance, response)
        results = run all tool calls concurrently (each with its own timeout)
        instance.add_tool_round(results, reasoning_details_from_response)
    # step cap reached: force a text answer
    return _finish(instance, chat_completion.create(..., tool_choice="none"))
```

- **`message`** is text stored as a user message at the start of the turn, for events that are not user messages, written by the caller including the `API:` prefix. It is stored after the lock is taken, so it cannot land in the middle of another turn. Use it for any new event source (reminders do). **`raise_errors=True`** lets `openai.APIError` escape instead of becoming a chat message, so a caller can fall back (reminders send plain text).
- **Maximum LLM calls per turn: `max_tool_steps + 1` (9).**
- The model is chosen **once per turn**, so every step of a tool loop uses the same model (reasoning blocks are bound to a model).
- **Forced final answer.** After the cap, one more request is made with the *same tool list* and `tool_choice="none"`. The tool list is kept (not removed) so the cached prefix stays valid; `tool_choice="none"` was verified accepted on all three models. Pending tool calls from the last round are not executed.
- There is **no overall deadline for a turn**. A turn can, in the worst case, take 9 LLM calls, each up to `llm_timeout_s` (180 s) times (1 + `llm_max_retries`) attempts, plus tool time.

## Finish reasons (`_finish`)

OpenRouter normalises `finish_reason` to `stop`, `length`, `tool_calls`, `content_filter`, `error`. Everything that is not a tool call goes through `_finish`:

| `finish_reason` | Content present? | Stored in context? | Returned to the chat |
|---|---|---|---|
| `stop` | yes | **yes** (`add_assistant_message`) | the model's text |
| `length` | yes | **yes** (the truncated text) | the truncated text |
| `length` | no | no | `LENGTH_MESSAGE` ("Sorry, my answer got cut off...") |
| `content_filter` | any | no | `REFUSAL_MESSAGE` |
| any, with `message.refusal` set | no content | no | `REFUSAL_MESSAGE` |
| `error` | any | no | `FALLBACK_MESSAGE` |
| `tool_calls` but the message has no tool calls | any | no | `FALLBACK_MESSAGE` |
| anything unknown | any | no | `FALLBACK_MESSAGE` |
| `stop` | no (empty or null) | no | `FALLBACK_MESSAGE` |

The empty-`length` case is real: reasoning shares `max_tokens` with the visible answer, so a too-small `max_tokens` ends with `length` and empty content while still being billed. Verified live by forcing `max_tokens=40` on Sonnet at `max` effort.

Error and fallback texts are **not stored** in the context, so the model never sees its own apologies and the next request is not polluted.

## API errors (`_error_message`)

Only `openai.APIError` and subclasses are caught. The SDK has already retried (see below) before they reach this point.

| Error | Reply | Log |
|---|---|---|
| HTTP 402 (insufficient credits) | `CREDITS_MESSAGE` | `CRITICAL` |
| HTTP 429 | `RATE_LIMIT_MESSAGE` | `ERROR` |
| timeout, connection error, or status >= 500 | `PROVIDER_MESSAGE` | `ERROR` |
| anything else (400, 401, 403, 404, ...) | `REQUEST_MESSAGE` ("Something went wrong with that request") | `ERROR` with the error text |

Note that a wrong API key (401) and a bad model slug (400) both surface to users as `REQUEST_MESSAGE`; the real cause is only in `app.log`. Verified live: an invalid slug produced a 400 and the generic message, and the user's message stayed in the context.

Exceptions that are not `openai.APIError` (a bug in this repo) propagate to the caller and end up in python-telegram-bot's error logging.

**Retries and timeouts** are configured on the SDK client in `chat_completion.py`: `timeout = llm_timeout_s` (180) and `max_retries = llm_max_retries` (2). The openai SDK retries connection errors, timeouts, 408, 409, 429 and 5xx with exponential backoff, honouring `Retry-After`. The retry path itself was not exercised against a real failing server (see [verification-status.md](verification-status.md)).

## Tool execution (`_process_tool_call`)

The tool calls of one response run **concurrently** with `asyncio.gather`; each is wrapped so it never raises:

1. `json.loads(arguments)`. On `JSONDecodeError`: the call is not executed, `{}` is stored as its arguments, and `{"ok": false, "error": "Invalid JSON in tool arguments: ..."}` is its result.
2. `asyncio.wait_for(registry.call(name, arguments, ToolContext(...)), tool_timeout_s)` (30 s). The registry validates arguments and wraps the result (see [`tools.md`](tools.md)). On timeout the coroutine is cancelled and the result is `{"ok": false, "error": "The tool timed out after 30 seconds."}`. Cancellation only takes effect at an `await`: a tool that blocks synchronously cannot be interrupted.
3. Any other exception: logged with a traceback, result is `{"ok": false, "error": "<ExceptionType>: <message>"}`.

The model sees the error as the tool result and can recover (retry, explain, try another tool). One slow tool does not delay the others' results being recorded beyond the longest timeout.

The returned tuples are `(tool_call_id, tool_name, arguments_dict, result)` in **call order**; `add_tool_round` writes the assistant message and all tool messages atomically (no `await` in between).

## Locking and overlap

`instance.lock` (an `asyncio.Lock`) is taken by the **callers** of `get_response`, not by `get_response` itself: `main.on_message` (from storing the user message until the reply is sent), `main.on_reminder` (the same, for the `API: ...` reminder message) and `main.toggle_always_on` (while it stores its notice). The lock is therefore held for the whole turn, including tool execution and the retries, and a second turn for the same chat waits for it.

**Why the caller.** The lock must cover storing the message too. Before #32 was fixed, `on_message` stored the message first and `get_response` took the lock afterwards: if another turn finished in between, its reply landed after the stored message and the waiting turn sent a request that **ended with an assistant message**, which Claude 5.5 rejects (HTTP 400). Nothing in code enforces that a new caller takes the lock; if you add one, take it before `add_user_message` and release it after the turn. See [known-issues.md](known-issues.md) item 1.

## Images

`assistant.encode_image(bytes) -> str`: opens with Pillow and returns a `data:image/jpeg;base64,...` URL. A JPEG whose long edge is at most `IMAGE_MAX_EDGE` (1568 px) is sent **byte for byte**, so it is not compressed a second time. Anything else (a larger image, or a PNG, WebP or other format) is converted to RGB, shrunk to fit 1568 px on the long edge with `thumbnail` (keeps the aspect ratio, never enlarges) and saved as JPEG at quality 95. Called by `main.on_message`. The stored image part is `{"type": "image_url", "image_url": {"url": ...}}` with **no `detail` field** (it did nothing on Claude). Bytes that are not a valid image raise from Pillow; that is not caught (inferred from the code, not tested).

**Why 1568 (issue #42, decided 2026-10-12).** The goal is for the bot to read small text on whiteboard and poster photos and set reminders from it. Anthropic's standard tier scales anything above 1568 px on the long edge down anyway, so a larger image only costs more tokens (about 4800 for 2560 px against about 2400 for 1568 px) for nothing. 512 px is far below that and was chosen for cost, not quality. A JPEG that already fits is passed through untouched because Anthropic warns that repeated lossy compression is what makes text unreadable. `client._handle_image` now downloads the largest Telegram size and leaves the capping to `encode_image`.

**What the poster test does and does not show.** Haiku 5.5 and Sonnet 5.5 were given a synthetic 2560 by 1920 poster with 12 times in 48, 30 and 20 px type (lightly blurred) and asked to list every HH:MM they could read, 2 runs each, with a large enough `max_tokens` (4000):

| Long edge sent | Prompt tokens | Haiku 5.5 cost | Sonnet 5.5 cost | Times read correctly (of 12) | Completion tokens (Haiku, Sonnet) |
|---|---|---|---|---|---|
| 512 (the old behaviour) | 315 | $0.00013 | $0.0026 | 12 and 12 (Haiku); 12 and 11 plus 1 invented (Sonnet) | 479-589, about 345 |
| 768 | 637 | | | 12 (all runs) | 185-239, 60 |
| 1280 | 1659 | $0.00020 | $0.0039 | 12 (all runs) | 60 |
| 1568 (chosen) | 2401 | $0.00027 | $0.0054 | 12 (all runs) | 60 to 240 |

**Honest reading: this test is too easy to prove that 1568 is needed.** Even the old 512 size read the poster in most runs. The visible differences are that at 512 the models had to think much harder (several times more output tokens, which are billed) and that Sonnet once missed a time and invented one. A synthetic poster in a clean font is much easier than a real photograph (glare, angle, handwriting, small print). The 1568 setting is therefore a judgement from the provider's own limits and Anthropic's compression advice, **not a measured requirement**. The first version of this section claimed 512 read none of the times; that was a measurement mistake (the reply budget was used up by reasoning, so the reply text came back empty) and has been corrected.

**If real photos prove hard to read,** raise `IMAGE_MAX_EDGE`, but the provider scales anything above 1568 px (standard tier) down regardless. The cost is paid on every request while the image stays in the history.

**End to end check (real `encode_image` and the real request, Telegram not involved):** a 4000 by 3000 poster photo was shrunk and sent, and the model read the shuttle time (07:55) correctly, computed the reminder time and noticed that 7 Nov 2026 is a Saturday, not a Friday as the poster says. The cache breakpoint sat on the image part.

**Earlier measurement of the old behaviour (checked 2026-10-12).** The photo used to be resampled twice before the model saw it (a Telegram size chosen by `client._handle_image`, then 512 by 512 at JPEG quality 75). OpenRouter's page "Image Inputs" accepts `image/png`, `image/jpeg`, `image/webp` and `image/gif` as data URLs or links and gives no size limit of its own; Anthropic's vision page lists 10 MB per image (base64) and 8000 by 8000 px. On a simple test image with one line of text, Haiku and Sonnet read it correctly even at 512, and `detail: "low"` made no difference on Claude. Token counts on that image: 512 by 384 gave 291 prompt tokens, 1280 by 960 gave 1635, and 2560 by 1920 or larger gave 4765. PNG and lossless WebP cost the same tokens as JPEG at the same size.

## Logging

`chat_completion.create` writes one INFO line per LLM call, after the response arrives:

```
llm_call {"chat_id": 7, "model": "anthropic/claude-haiku-5.5", "effort": "low", "prompt_tokens": 16,
          "completion_tokens": 10, "reasoning_tokens": 0, "cached_tokens": 0, "cost": 6.6e-06,
          "latency_s": 2.047, "tool_calls": [], "finish_reason": "stop"}
```

`cost` is in OpenRouter credits (dollars). Any usage field can be `null` if the provider omitted it. Failed calls (exceptions) write no `llm_call` line; the error is logged by `_error_message`. Grep `app.log` for `llm_call` to total spend or check `cached_tokens`.
