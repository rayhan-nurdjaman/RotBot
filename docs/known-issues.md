# Known issues and traps

Everything here was **reproduced or read directly in the code** on commit `3d28d30` plus the reminder rewrite (items marked fixed since then are removed), unless marked *inferred*. Items are ordered by how likely they are to hurt. None of these are tracked as GitHub issues unless a number is given. If you fix one, delete it here.

## Security (by decision, not a bug)

- **The `bash` tool has no sandbox.** The model can run commands as the bot's user, and the bot's environment contains the OpenRouter and Telegram keys. The owner accepted this on 2026-10-12. Two things now limit who can reach it (#35, see [`access-control.md`](access-control.md)): only user ids in `ALLOWED_USER_IDS` can use the bot at all, and the tool only runs in chats listed in `OP_CHAT_IDS`. Anyone who is allowed **in an op chat** still has a shell with your keys, and so does anything the model is tricked into doing there. Details and the limits on the tool itself are in [`tools.md`](tools.md#bash-a-shell-with-no-sandbox-added-by-the-owners-decision-2026-10-12).

## Bugs (wrong behaviour today)

### 1. Two quick messages in one chat make the second reply fail

**Symptom.** A user sends two messages quickly. They get one answer to the first, then `Something went wrong with that request. Please try again.` for the second.

**Cause.** `main.on_message` adds the user message to the context **before** waiting for the chat lock. The first turn finishes and appends its answer (an `assistant` message). The second turn then starts, and its request ends with that `assistant` message. Claude 5.5 rejects this with HTTP 400 "This model does not support assistant message prefill. The conversation must end with a user message." The `try/except` turns it into the generic error text and **nothing is stored**, so the context now ends with an assistant message until the next user message arrives.

**Verified live** on Haiku, Sonnet and Opus (`last message role = ['user', 'assistant']`, second reply is the generic error).

**When it can actually happen.** python-telegram-bot's `max_concurrent_updates` is 1 (verified), so two user messages from Telegram are processed strictly one after the other and this race does not occur between two Telegram updates. It will occur if `concurrent_updates` is ever raised. **Reminders no longer cause it**: `main.on_reminder` passes its `API:` message to `get_response(instance, message=...)`, which stores it only after taking the chat lock. Treat the user-message half as a latent bug that bites as soon as concurrency is raised.

**Fix direction (not done for user messages).** Do for `main.on_message` what `on_reminder` already does: hand the text to `get_response(instance, message=...)` so it is stored under the lock. Or queue messages (planned in #21).

### 2. No spend cap, and every allowed user can change the model

Only users in `ALLOWED_USER_IDS` can use the bot (#35, [`access-control.md`](access-control.md)). Among them there are **no per-user permissions**: any of them can run `/model opus-5.5-max` (about 40 times the per-token price of the default, and `max` effort uses a lot of reasoning tokens) and toggle `/autoroute`, `/stfu` and `/schizo` for any chat they are in. There is also no spend cap in the program (planned: #14, #31). The only limit is the key limit set in the OpenRouter dashboard.

### 3. Server-tool results are not in the history, so the model may deny having used them

`web_search` and `web_fetch` run on OpenRouter (see [`tools.md`](tools.md)). Their calls and results are not in the assistant message we store, only the final cited reply. On a later turn the model sees a reply that cites sources but no tool call, and when asked "did you actually search?" it may answer that it did not, or that it cannot verify (reproduced live on Haiku, including after adding a prompt hint, which was removed because it had no effect). The earlier reply is still correct; only the model's account of how it got there is wrong. Also, a function-tool call after a server-tool call in the same turn does not see the search in the replayed history. Fix directions: store the `annotations` text in the assistant message, or accept it.

### 4. Long or empty replies can fail to send (fixed, #37)

`client.send_message` now splits replies into pieces of at most 4096 characters, skips empty replies, logs why Telegram rejected a Markdown send, and `main.on_message` catches and logs a failed send. Tested with a stand-in bot, not against real Telegram. Remaining: a send that fails halfway through a long reply leaves the first pieces in the chat, and the reply is not retried for user messages (it is for reminders, which would resend the earlier pieces).

### 5. Users without usernames are indistinguishable (and one case crashes)

The speaker name in history is the Telegram `username`. It is `None` for users who have none (text becomes `None: hi`), and two such users look identical to the model. A reply to a message with no sender raises `AttributeError` in `client.on_message` (verified earlier; still possible, the allow-list does not prevent it). A message with `from_user is None` (anonymous group admin) used to raise the same error; it is now dropped by the access gate before it gets there (shown by a test through PTB's dispatcher). Memory is per chat, so facts saved about "alice" in a group are visible to everyone in it.

## Surprises (behave as written, but not what you would assume)

- **Commands not registered are answered as chat text.** `/aura` (removed) or `/stfu@another_bot` goes to the model. See [`telegram-client.md`](telegram-client.md).
- **One timezone for every chat.** `timezone` in `config/config.yaml` (default `Asia/Singapore`) is used for the message timestamps and for reminder times that have no UTC offset. There is no per-chat timezone.
- **`/stfu` messages enter the context** as `API:` user messages and the model sees them. They are `user` messages, so a long run of them is still valid for the API.
- **Reminders have no `created_by`.** The column exists but is never filled: a tool only knows the chat, not the sender. The model is told to put the requester's username in the description, and the reminder text tags them from that (same as before the rewrite). Threading the sender through `get_response` into `ToolContext` is the fix if it is ever needed (also see item 5).
- **Memory has no `created_by` either, and anyone in a chat can edit it.** Same cause as reminders. `/memory` and `/forget` have no permission check (see item 2), so any member can delete a memory another member saved, including standing instructions. A memory is also text the model reads as instructions, so a member can change how the bot behaves for the whole chat by saving one. That is the intended way to change tone or style, but it is also how a prank or a prompt injection persists. Memories are capped (300 characters, 200 per chat, 50 shown) to bound the cost.
- **The secrets filter is a safety net, not a guarantee.** `memories.add` rejects obvious card numbers, `sk-` keys, private key headers and "password is ..." phrasing, and nothing else.
- **`memory.json` is ignored.** Memory used to be in `data/memory.json`; there is no import. Anyone with an old file has to re-save what matters.
- **Reasoning is never shown**, and `reasoning` text is not logged either; only `reasoning_tokens` counts appear in `llm_call`.
- **The wrong API key looks like a generic error to users.** Check `app.log`.
- **Cost is invisible to users.** Only the `llm_call` log line has it.
- **`app.log` contains every message and reply in plain text**, with no rotation or retention. See the README privacy section.
- **Image size is a cost trade-off.** Images are capped at 1568 px on the long edge (`assistant.IMAGE_MAX_EDGE`) so the bot can read small print on posters and whiteboards (#42, details in [`agent-loop.md`](agent-loop.md#images)). That is about 2400 prompt tokens per image, **paid again on every request while the image stays in the history**. Only a synthetic poster was tested, and it was easy enough that even the old 512 px size read it in most runs, so 1568 is a judgement and not a measured requirement; real photos (glare, angle, handwriting) were not tested. An image sent as a file (document) rather than as a photo looks like it is ignored, because `client.on_message` only reads `message.photo` (*inferred from the code, not tested*).
- **Image handling.** Images are stored inline as base64 in the context and re-sent on every request until trimmed; the base64 text (about 100 KB for a large photo) sits in memory. `encode_image` is not guarded against non-image bytes (*inferred*).
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
| `Instance.enable_vision`, `.modalities` | `src/instance.py` | Set, never read. |
| `client.send_gif`, `send_image`, `send_client_message` | `src/client.py` | No callers. |
| `message_id` parameters and the unused `message_id` local in `client.on_message` | `src/client.py` | Replies are not threaded. |
| Commented-out token counting | `Instance.add_to_context`, `_set_personality` | Left from before. |
| `global instances` | `main.get_instance` | Unneeded; pyflakes warns. |
| `README.md` TODO list | root | Owner's own notes; partly stale. |
| `unused/` | repo | Retired features. `image_generation.py` still reads `OPENAI_API_KEY`. |

`python -m pyflakes src` currently reports exactly two warnings (the unused `message_id` and `global instances`); keep it that way or lower.
