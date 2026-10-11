# Known issues and traps

Everything here was **reproduced or read directly in the code** on commit `71bbe7d` (items marked fixed since then are removed), unless marked *inferred*. Items are ordered by how likely they are to hurt. None of these are tracked as GitHub issues unless a number is given. If you fix one, delete it here.

## Bugs (wrong behaviour today)

### 1. Two quick messages in one chat make the second reply fail

**Symptom.** A user sends two messages quickly. They get one answer to the first, then `Something went wrong with that request. Please try again.` for the second.

**Cause.** `main.on_message` adds the user message to the context **before** waiting for the chat lock. The first turn finishes and appends its answer (an `assistant` message). The second turn then starts, and its request ends with that `assistant` message. Claude 5.5 rejects this with HTTP 400 "This model does not support assistant message prefill. The conversation must end with a user message." The `try/except` turns it into the generic error text and **nothing is stored**, so the context now ends with an assistant message until the next user message arrives.

**Verified live** on Haiku, Sonnet and Opus (`last message role = ['user', 'assistant']`, second reply is the generic error).

**When it can actually happen.** python-telegram-bot's `max_concurrent_updates` is 1 (verified), so two user messages from Telegram are processed strictly one after the other and this race does not occur between two Telegram updates. It **can** occur between a **reminder** (fired by the polling loop, which runs `on_reminder` concurrently with the update handler) and a user message in the same chat, and it will occur if `concurrent_updates` is ever raised. Treat it as a latent bug that bites as soon as concurrency is raised.

**Fix direction (not done).** Make `get_response` skip the LLM call when the context already ends with an `assistant` message, or take the lock before appending the user message, or queue messages (planned in #21).

### 2. `set_reminder` errors and the poll loop

`tools/time_manager.poll` iterates `for instance_id in reminders:` and awaits `on_reminder` inside the loop. Two problems, both reproduced:

- **`RuntimeError: dictionary changed size during iteration`** if a reminder for a **new chat** is created while `poll` is awaiting a callback for another chat (the model can call `set_reminder` during that turn, from a different task). Reminders for chats that already have an entry are safe.
- **Any exception from `on_reminder`** (for example Telegram failing to send, or an LLM bug) is not caught in `poll` or in `main.main()`'s loop (which only has `try/finally`). It **ends the whole program**. The failing reminder stays queued.

Also: a reminder is removed only **after** `on_reminder` returns, and `on_reminder` takes a whole turn, so a reminder can be delivered twice if the program restarts in between (inferred: reminders are lost on restart anyway, so this does not apply today).

### 3. Reminders and instances are lost on restart

All of: conversation history, `/model`, `/autoroute`, `/stfu`, `/schizo`, and **pending reminders** live in process memory. A restart (including a crash from item 2) silently drops every pending reminder with no notice to users. Only `data/memory.json` persists. Planned: #18.

### 4. A new chat starts as `base`, not `rotbot`; a failed `/schizo` resets the chat

`Instance.__init__` is written to default to the `rotbot` persona but actually runs `base` (see [`configuration.md`](configuration.md)). Verified: a new `Instance` has `personality == 'base'`, empty custom instruction, `context_length` 8.

Separately, `Instance.set_personality` assigns `self.personality = key` **before** looking the key up, and `main.set_instance_personality` calls `set_personality('base')` first. So `/schizo nope` on a chat that was `british` leaves it as `base` with `instance.personality == 'nope'` and `context_length` 8, while telling the user "The personality 'nope' does not exist." The chat lost its persona. Verified. Via Telegram, `/schizo` lowercases the first argument and ignores the rest, so `/schizo British` and `/schizo british extra` both select `british`.

### 5. Unauthenticated access and cost

No chat allow-list and no per-user permissions anywhere. Anyone who finds the bot (or is in a group that has it) can use it, run `/model opus-5.5-max` (about 40 times the per-token price of the default, and `max` effort uses a lot of reasoning tokens), and toggle `/autoroute`. There is also no spend cap in the program (planned: #14, #31). The only limit is the key limit set in the OpenRouter dashboard. A hostile or curious user can burn it down.

### 6. Server-tool results are not in the history, so the model may deny having used them

`web_search` and `web_fetch` run on OpenRouter (see [`tools.md`](tools.md)). Their calls and results are not in the assistant message we store, only the final cited reply. On a later turn the model sees a reply that cites sources but no tool call, and when asked "did you actually search?" it may answer that it did not, or that it cannot verify (reproduced live on Haiku, including after adding a prompt hint, which was removed because it had no effect). The earlier reply is still correct; only the model's account of how it got there is wrong. Also, a function-tool call after a server-tool call in the same turn does not see the search in the replayed history. Fix directions: store the `annotations` text in the assistant message, or accept it.

### 7. Long or empty replies can fail to send

`client.send_message` has no splitting. A reply over 4096 characters or an empty reply raises `telegram.error.BadRequest` after the plain-text retry (verified). The raise is not caught in `main.on_message`; the reply is lost and already stored in the context as if sent. `max_tokens` is 16000, so a long answer can exceed the limit. Empty replies are rare because `_finish` substitutes a fallback message for empty content.

### 8. Users without usernames are indistinguishable (and one case crashes)

The speaker name in history is the Telegram `username`. It is `None` for users who have none (text becomes `None: hi`), and two such users look identical to the model. A message with `from_user is None` (anonymous group admin) or a reply to a message with no sender raises `AttributeError` in `client.on_message` (verified). Memory is per chat, so facts saved about "alice" in a group are visible to everyone in it.

## Surprises (behave as written, but not what you would assume)

- **Commands not registered are answered as chat text.** `/aura` (removed) or `/stfu@another_bot` goes to the model. See [`telegram-client.md`](telegram-client.md).
- **`web_search` without Google keys looks like "no results".** The keys are not in `.env.example`. See [`tools.md`](tools.md).
- **`get_time` is always UTC+8.** There is no per-chat timezone. Reminders use the same offset. The default instance also has an unused `zone_offset = 8`.
- **`/stfu` messages enter the context** as `API:` user messages and the model sees them. They are `user` messages, so a long run of them is still valid for the API.
- **A reminder in the past is accepted** and fires within about a second.
- **Reasoning is never shown**, and `reasoning` text is not logged either; only `reasoning_tokens` counts appear in `llm_call`.
- **The wrong API key looks like a generic error to users.** Check `app.log`.
- **Cost is invisible to users.** Only the `llm_call` log line has it.
- **`app.log` contains every message and reply in plain text**, with no rotation or retention. See the README privacy section.
- **Image handling.** Images are stored inline as base64 in the context and re-sent on every request until trimmed; at `detail: low` this is cheap in tokens, but the base64 text sits in memory. `encode_image` is not guarded against non-image bytes (*inferred*).
- **Tests do not cover Telegram or OpenRouter networking.** See [`verification-status.md`](verification-status.md).
- **The chunked trim means an extra half-window of old messages is sent** between trims (up to `context_length + int(context_length * 0.5)` messages), which is deliberate.
- **Python 3.12 is required.** The code uses f-strings that reuse the same quote character inside the braces (`f'... \'{d['x']}\' ...'`), which is a syntax error before 3.12.
- **Indentation is tabs in `src/`, 4 spaces in `tests/`.** Match the file you edit.

## Dead code and leftovers (safe to delete when touching them)

| Item | Where | Note |
|---|---|---|
| `class Reminder` | `src/instance.py` | Raises `AttributeError` if instantiated; nothing uses it. |
| `class CompletionStream` | `src/chat_completion.py` | Empty placeholder for streaming (#27). |
| `Instance.add_system_message` | `src/instance.py` | No callers. |
| `Instance.enable_vision`, `.modalities`, `.zone_offset` | `src/instance.py` | Set, never read. |
| `client.send_gif`, `send_image`, `send_client_message` | `src/client.py` | No callers. |
| `message_id` parameters and the unused `message_id` local in `client.on_message` | `src/client.py` | Replies are not threaded. |
| Commented-out token counting | `Instance.add_to_context`, `_set_personality` | Left from before. |
| `global instances` | `main.get_instance` | Unneeded; pyflakes warns. |
| `now_utc` unused variable | `tools/time_manager.add_reminder` | pyflakes warns. |
| Bare `except:` | `client.send_message`, `time_manager.add_reminder` | Swallow everything, including cancellation. |
| `README.md` TODO list | root | Owner's own notes; partly stale. |
| `unused/` | repo | Retired features. `image_generation.py` still reads `OPENAI_API_KEY`. |

`python -m pyflakes src` currently reports exactly three warnings (the unused `message_id`, `global instances`, and `now_utc`); keep it that way or lower.
