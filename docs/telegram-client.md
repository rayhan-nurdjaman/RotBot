# Telegram client

`src/client.py` is the only module that knows about Telegram. It uses python-telegram-bot 21.10 (`telegram`, `telegram.ext`) with **long polling** (`updater.start_polling(drop_pending_updates=True)`): the program asks Telegram for updates; no webhook, no public URL needed.

## Import-time side effects

`client.py` builds `application = telegram.ext.Application.builder().token(api_key).build()` and registers all handlers **when it is imported**. Consequences:

- `TELEGRAM_API_KEY` must be in the environment before the import (an empty value raises `InvalidToken`). `main.py` calls `load_dotenv()` first for this reason.
- Tests set a dummy token in `tests/conftest.py` and **replace `client.application` or its methods** instead of connecting.
- PTB's bot object is frozen: you cannot assign to `client.application.bot.send_message`. Replace `client.application` with a stand-in object (`SimpleNamespace(bot=...)`) in tests.

## Handlers (registered in this order, all in group 0)

1. `CommandHandler('stfu', ...)`
2. `CommandHandler('model', ...)`
3. `CommandHandler('autoroute', ...)`
4. `CommandHandler('schizo', ...)`
5. `CommandHandler('memory', ...)`
6. `CommandHandler('forget', ...)`
7. `MessageHandler(filters.ALL, on_message)`

PTB runs **only the first matching handler per group**. A command message matches both its command handler and the catch-all; the command handler wins because it is registered first. **Keep the catch-all last.** A slash command that is not registered (including a command addressed to another bot, `/stfu@other_bot`, and removed ones like `/aura`) is handled by the catch-all as **ordinary text**, so the bot may reply to it with the model. (Verified by matching real `Update` objects.)

PTB is used with the default `max_concurrent_updates = 1`: updates are processed **one at a time, in order**. While one turn runs (model calls and tools, possibly tens of seconds), no other chat's message is handled. See [`architecture.md`](architecture.md).

## Incoming messages (`client.on_message`)

Behaviour, in order:

1. `update.message is None` (edited messages, channel posts, callback queries, ...): ignored. Edited messages are therefore never seen.
2. `username = update.message.from_user.username`. This is **`None` for users without a Telegram username**, and the text then starts with `"None: "`. Chat history has no other notion of who spoke (no user id). Two users without usernames are indistinguishable. `from_user` can itself be `None` for anonymous admin or channel senders; that raises (not handled).
3. **Reply prefix.** If the message replies to another: `*replying to '<replied text or caption>' by <replied username>* ` is prepended. It uses `reply.caption` if the **new** message has a photo, else `reply.text`. If the replied-to message has neither, the prefix says `None`. If `reply.from_user` is missing it raises (not handled).
4. **Text.** `text` is used; if there is also a `caption`, **the caption replaces the text**. (A message cannot normally have both.)
5. **Nothing to say.** If there is no text/caption **and** no photo (stickers, voice, video, documents, polls, locations, ...): ignored, no reply.
6. **Photo.** If the message has a photo: pick a size (see below) and download it into memory. On `TelegramError` the failure is logged and the message continues as text only; if there is no text either, it is ignored.
7. Calls `on_message_callback(chat_id, "<username>: <reply prefix><text>", image_bytes_or_None, no_response=False)`.

Photo size selection: always the **largest** size Telegram offers (`message.photo[-1]`). `assistant.encode_image` then caps it at 1568 px on the long edge (a JPEG that already fits is passed through untouched). Albums (several photos sent together) arrive as separate updates, one photo each; each triggers its own reply. Documents sent as files (not photos) and animated stickers are ignored. A caption on a non-photo (for example a video caption) is treated as text. There is no handling for message length: a very long incoming text is passed to the model as is.

## Outgoing messages

| Function | Behaviour |
|---|---|
| `send_message(chat_id, text)` | Sends with `parse_mode=MARKDOWN` (Telegram's **legacy** Markdown). On **any** exception it retries once as plain text. If that fails too, the exception propagates. |
| `send_client_message(chat_id, text)` | `send_message` with the text wrapped in `_..._` (italics). Unused now. |
| `send_image`, `send_gif` | Thin wrappers over `send_photo` / `send_animation`. **Unused** (their tools were retired). |
| `send_indicator(instance, indicator)` | Only `GENERATING_RESPONSE` does anything (sends the `typing` chat action). `REMINDER_CREATED` is accepted and ignored. |
| `typing(instance)` | `async with` context manager: re-sends `typing` every 4 seconds while the body runs, cancels on exit or error, never raises (indicator errors are logged and ignored). Used around every turn. |

Verified with a stand-in bot: plain text goes out in 1 attempt; text that Telegram's Markdown parser rejects (for example unbalanced `_`) is sent a second time as plain text; an **empty** message and a message **over 4096 characters** fail both attempts and raise `BadRequest` out of `send_message`. **There is no splitting of long replies and no handling of empty ones** (max reply is bounded in practice by `max_tokens`, 16000 tokens, which can exceed 4096 characters). A raise there propagates out of `main.on_message` into PTB's error handling (the reply is lost; the assistant message is already stored in the context).

The `message_id` parameter of the send functions is accepted and ignored: replies are **not** threaded to the user's message.

## Commands

Handlers in `client.py` only forward to callbacks; the logic is in `main.py`.

| Command | Handler | Callback in `main` | Arguments |
|---|---|---|---|
| `/help` | `help` | `show_help(chat_id)` | none; sends `main.HELP_TEXT` |
| `/stfu` | `toggle_always_on` | `toggle_always_on(chat_id)` | none |
| `/model` | `model` | `set_instance_model(chat_id, text)` | all arguments joined with spaces |
| `/autoroute` | `autoroute` | `toggle_auto_route(chat_id)` | none |
| `/schizo` | `schizo` | `set_instance_personality(chat_id, key)` | first argument, lowercased; none passes an empty key and `main` lists the available personalities |
| `/memory` | `memory` | `list_memories(chat_id)` | none |
| `/forget` | `forget` | `forget_memory(chat_id, text)` | all arguments joined with spaces; `main` parses an integer id |

Command arguments are split on whitespace by PTB, so `/model haiku-5.5 high` passes the text `haiku-5.5 high` which is **not** valid (the format is `haiku-5.5-high`). There is no permission check on any command: anyone in a chat can toggle always-on, change the model (and so the cost) and change the persona. There is also no allow-list of chats: **any chat that finds the bot can use it and spend credit**.

To add a command: (1) add a callback variable at the top of `client.py` (`my_callback = None`), (2) add an `async def` handler that calls it, (3) register it with `CommandHandler` **before** the catch-all, (4) write the logic in `main.py` and assign it in `main.main()` next to the other callbacks, (5) add tests (see [`contributing.md`](contributing.md)). The bot does not register its command list with Telegram (`set_my_commands`), so commands do not appear in Telegram's `/` menu.

## Formatting the model's output

The model is told to use Markdown. Telegram's legacy Markdown supports only `*bold*`, `_italic_`, `` `code` `` and `[links](url)`, and is strict: a stray `_` or `*` makes Telegram reject the message, triggering the plain-text retry (so the user sees raw `**` characters instead of bold). Models often write `**bold**` and `- lists`, which legacy Markdown does not render. This is cosmetic, not an error.

## Where the bot sees messages

The bot answers every message it receives in any chat (unless always-on is off for that chat). In **groups** it only receives messages Telegram delivers to bots: by default bots in groups with privacy mode on see only commands and replies to the bot; to see all messages the bot's privacy mode must be disabled in BotFather. This is Telegram configuration, not code, and has not been tested because the bot is not set up on Telegram at the time of writing.
