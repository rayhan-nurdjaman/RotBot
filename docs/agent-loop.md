# The agent loop

Implemented in `src/assistant.py`. Entry point: `assistant.get_response(instance) -> str`. It always returns a string to send to the chat (a model answer or a short error message) and only raises for bugs, not for API failures.

## Algorithm

```text
get_response(instance, message=None, raise_errors=False):
    async with instance.lock:                         # one turn per chat at a time
        if message:
            instance.add_user_message(message)        # stored under the lock, see below
        try:
            return await _run_turn(instance)
        except openai.APIError as e:
            if raise_errors: raise                    # for callers with their own fallback (reminders)
            return _error_message(e)                  # nothing stored; user's message stays

_run_turn(instance):
    instance.drop_reasoning()                         # reasoning blocks never cross turns
    model = router.route(instance) if instance.auto_route else instance.model   # once per turn
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

`instance.lock` (an `asyncio.Lock`) is held for the whole turn, including tool execution and the retries. A second turn for the same chat waits for it.

**Caveat.** The lock serialises *LLM work*, not *what is in the context*. If a user message is appended to the context while another turn is waiting for the lock, the finished turn's reply is appended after it and the waiting turn then sends a request that **ends with an assistant message**, which Claude 5.5 rejects. See [known-issues.md](known-issues.md) item 1. Reminders avoid this: `get_response(instance, message=...)` stores its message only after taking the lock.

## Images

`assistant.encode_image(bytes) -> str`: opens with Pillow, shrinks to fit within 512 by 512 (`thumbnail`, keeps aspect ratio), re-encodes as JPEG, and returns a `data:image/jpeg;base64,...` URL. Called by `main.on_message`. Messages with images are stored with `detail: "low"`. Bytes that are not a valid image raise from Pillow; that is not caught (inferred from the code, not tested).

## Logging

`chat_completion.create` writes one INFO line per LLM call, after the response arrives:

```
llm_call {"chat_id": 7, "model": "anthropic/claude-haiku-5.5", "effort": "low", "prompt_tokens": 16,
          "completion_tokens": 10, "reasoning_tokens": 0, "cached_tokens": 0, "cost": 6.6e-06,
          "latency_s": 2.047, "tool_calls": [], "finish_reason": "stop"}
```

`cost` is in OpenRouter credits (dollars). Any usage field can be `null` if the provider omitted it. Failed calls (exceptions) write no `llm_call` line; the error is logged by `_error_message`. Grep `app.log` for `llm_call` to total spend or check `cached_tokens`.
