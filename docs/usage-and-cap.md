# Usage tracking and the daily spend cap

Implemented in `src/usage.py`, table `usage` (migration 3 in `src/db.py`), setting `daily_cap_usd` in `config/config.yaml`. Issues #14 and #31.

## What is stored

Every successful LLM call writes one row. `chat_completion.create` does it right after the `llm_call` log line, so **every** call is covered in one place: each request of a tool loop, the forced final answer, reminder turns.

| Column | Meaning |
|---|---|
| `chat_id` | The chat the call was made for (`instance.unique_id`). A call with no chat id is not stored. |
| `created_at` | Unix seconds, UTC. |
| `model`, `effort` | The slug and reasoning effort that were sent. |
| `prompt_tokens`, `completion_tokens`, `reasoning_tokens`, `cached_tokens` | From OpenRouter's usage object. `NULL` if the provider omitted it. |
| `cost` | USD, OpenRouter's `usage.cost`. `NULL` if missing; counts as 0 in totals. |

Failed calls (exceptions) write no row, because OpenRouter returned no usage for them. `usage.record` never raises: if the insert fails it logs a traceback and the turn goes on, since the call was already paid for and its numbers are in `app.log`.

## The daily cap

- **Amount:** `daily_cap_usd` in `config/config.yaml` (1.00 dollar). **Per chat**, no global cap.
- **Day:** a calendar day in the configured `timezone`, so it resets at local midnight (`usage._midnight`).
- **When it is checked:** once at the start of `assistant.get_response`, before the model is called. If the chat has spent **the cap or more** today, `get_response` returns `assistant.CAP_MESSAGE` ("This chat has reached its spending limit for today. It resets at midnight.") and makes no model call. The user's message stays in the context, like after an API error.
- **Overshoot is accepted by decision.** A turn that starts just under the cap runs to the end, so a chat can end the day a little over it (up to one turn, which with a long tool loop on Opus can be tens of cents). There is no mid-turn check.
- **Reminders over the cap:** `main.on_reminder` runs its turn with `raise_errors=True`; `CapReached` is caught next to `openai.APIError` and the reminder is sent as plain text (`Reminder: <description>`), so it is not lost.
- **Database unreadable:** `usage.check` logs and lets the turn go ahead (the same choice as `Instance.load_memory`). A broken database does not silence the bot, but it also disables the cap until it is fixed.
- **Not covered:** the cap is on LLM cost as reported by OpenRouter. Server tools (search, fetch) are billed into the same `cost`. Spend by other keys or outside the bot is not seen. It does not replace the hard limit on the key in the OpenRouter dashboard, which the bot cannot bypass (currently $50).
- **Changing it:** edit the YAML and restart. There is no per-chat override and no command to change it.

## `/usage`

Open to every allowed user, and shows only the chat it is sent in (`main.show_usage(chat_id)` passes the chat id of the update; nothing takes an id from the message text). Output:

```text
Usage of this chat (daily cap $1.00, resets at midnight Asia/Singapore):
Today: $0.2500 in 3 calls, $0.7500 left
Last 7 days: $0.7500 in 12 calls
All time: $2.7500 in 40 calls
```

"Last 7 days" is today and the six days before it, counted from local midnight. The command does not take the chat lock and does not call the model.

## Tests and verification

`tests/test_usage.py` (17 tests, real SQLite file in a temp folder, fake clock): all fields stored, NULLs, per-chat totals, the midnight boundary, the cap at and below the limit, per-chat cap, reset at midnight, database failure, the report, `create` recording, a turn over the cap making no model call, a reminder over the cap going out as plain text, `/usage` isolation. Five deliberate breaks of the code (cap never fires, totals ignoring the chat, wrong midnight, `create` not recording, no check) each made at least one test fail.

Live (2026-10-12, Haiku 5.5, temp database, Telegram not involved): with the cap set to $0.0005, a real turn answered and stored one row with the cost OpenRouter reported ($0.00074); the next turn returned the cap notice without a request; another chat's total stayed 0. **Not verified:** `/usage` through real Telegram.

## Things to know

- Rows are never deleted. A chat with heavy use grows the table by one row per call; the index on `(chat_id, created_at)` keeps the queries cheap. Nothing prunes it.
- The cap compares floats from OpenRouter; tiny rounding at the exact limit does not matter.
- Spend before migration 3 (everything in `app.log` up to now) is not in the table.
- `/usage` shows the cap from the config, not what remains in the OpenRouter account.
