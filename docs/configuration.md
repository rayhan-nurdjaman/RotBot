# Configuration

Everything is read **once at import or instance creation** and not watched. After editing a config file, restart the program (and note that existing `Instance`s keep the values they already loaded; instances are lost on restart anyway).

All paths are relative to the repository root, which must be the working directory.

## Environment variables (`.env`)

Loaded by `load_dotenv()` in `main.py` before the modules that need them are imported. `.env` is gitignored. `.env.example` lists the four below.

| Variable | Required | Used by | If missing or wrong |
|---|---|---|---|
| `TELEGRAM_API_KEY` | **yes** | `client.py` (at import) | Import raises `telegram.error.InvalidToken`. A well-formed but wrong token starts, then fails with `InvalidToken: Unauthorized`; the extra `RuntimeError: This Updater is not running!` after it is a side effect, ignore it. |
| `ALLOWED_USER_IDS` | **yes** | `access.py`, read by `main.main()` at startup | Empty, missing, or containing anything that is not a positive number (a username, a group id): **the bot refuses to start** with a message saying which. Comma separated numeric Telegram **user** ids. See [`access-control.md`](access-control.md). |
| `OP_CHAT_IDS` | no | `access.py` | Empty or missing: the `bash` tool is off in every chat. Comma separated numeric chat ids (a group id is negative; in a private chat the chat id equals the user id). A bad entry stops the bot at startup. |
| `OPENROUTER_API_KEY` | **yes** | `chat_completion.py` (at import) | Unset: import raises `OpenAIError`. Empty or wrong: every LLM call fails (HTTP 401) and users see "Something went wrong with that request." |

`GOOGLE_API_KEY` and `GOOGLE_SEARCH_ENGINE_ID` are no longer used: web search runs on OpenRouter, billed to the same key. `OPENAI_API_KEY` is no longer read by anything in `src/` (only by retired code in `unused/`). `OPS` (an admin list of usernames for the removed `/sql` command) is not used; `ALLOWED_USER_IDS` and `OP_CHAT_IDS` replace the idea, with numeric ids instead of usernames.

## `config/config.yaml`

Loaded into the dict `config.config` by `src/config.py` when first imported. Every key below is required; a missing key raises `KeyError` at import.

| Key | Value | Meaning |
|---|---|---|
| `max_tool_steps` | 8 | Maximum tool rounds per turn. After that one more call is made with `tool_choice="none"`. |
| `context_trim_fraction` | 0.5 | The history may grow by `int(context_length * this)` messages (minimum 1) past `context_length` before it is cut back. Lower means more frequent trims, so more cache misses. |
| `max_tokens` | 16000 | Output limit per LLM call, **shared by reasoning and the visible reply**. |
| `llm_timeout_s` | 180 | HTTP timeout per LLM request attempt. |
| `llm_max_retries` | 2 | SDK retries for timeouts, connection errors, 408, 409, 429, 5xx (exponential backoff). |
| `tool_timeout_s` | 30 | Per tool call. A timed-out tool returns an error to the model. |
| `daily_cap_usd` | 1.00 | The most one chat may spend per calendar day (in `timezone`), in USD. Checked at the start of a turn, so one turn can overshoot. See [`usage-and-cap.md`](usage-and-cap.md). |

There is no per-model, per-chat or per-persona config. Removed keys (do not re-add without wiring them up): `poll_rate`, `client_backend`, `assistant_backend`. The reminder scheduler has no config: its timings (`MAX_ATTEMPTS`, `RETRY_DELAYS`, `MAX_SLEEP`, `CRASH_RETRY`) are constants in `src/reminders.py`. Tests read these values through the `config` dict, so changing a value in the file can change test expectations that are derived from it.

## Personalities (`config/personality/`)

`index.yaml` maps a name to a file:

```yaml
base: base.yaml
rotbot: rotbot.yaml
british: british.yaml
caveman: caveman.yaml
classic: classic.yaml
lockedin: lockedin.yaml
```

Names are matched **exactly and case-sensitively** against these keys, but `/schizo` lowercases the first argument and ignores the rest. Adding a personality: create the YAML, add one line to `index.yaml`.

### File keys

| Key | In | Effect |
|---|---|---|
| `system_prompt` | `base.yaml` only | The static system prompt. Every persona inherits it. |
| `custom_instruction` | any | Appended directly after `system_prompt` (plain string concatenation, **no separator**; start the text with a newline or heading). `base.yaml` has `''`. |
| `context_length` | any | Maximum history length in **messages** (see [`context-and-caching.md`](context-and-caching.md)). `base`: 8, `lockedin`: 16, the others: 24. |
| `modalities` | `base.yaml` | Loaded into `instance.modalities` but **read by nothing**. Ignored. |

A persona file may omit any key; omitted keys keep the value from the previously loaded file. `model:` and `temperature:` keys were removed on purpose (models are chosen only through `/model` and the router; Claude 5.5 rejects temperature). If you add a `model:` key to a YAML it is silently ignored.

### How a persona is applied

`Instance.set_personality(key)` loads `base` and then the persona's file on top of it, so nothing of the previous persona stays (a key that a file omits keeps the `base` value). Everything is read before anything is changed: an unknown key (`KeyError`) or a missing file (`FileNotFoundError`) leaves the chat exactly as it was.

A new `Instance` starts as `rotbot` (`Instance.__init__` calls `set_personality('rotbot')`).

`/schizo <name>` (via `main.set_instance_personality`) calls `set_personality(name)`. It does **not** clear the conversation history. Earlier messages written in the old persona stay in the context and can influence the new one (few-shot contamination; the README TODO mentions compaction as a future idea). On an unknown name it replies `The personality '<name>' does not exist.` and the chat **keeps the persona it had**.

The system prompt text itself contains: identity and role (RotBot, username `rotrot_botbot`, replies in group chats formatted as `<username>: <message>`), brevity and Markdown guidance, a source-citing format, a capabilities list (vision, including small text on posters; images over 1568 px are scaled down), a `# Tools` section, a `# Time` section (explains the `[date time]` stamp on each user message, with the `{timezone}` placeholder filled from `config.yaml`), and a memory paragraph pointing to the `API: Memory` message that follows the system prompt. The `# Tools` section is the placeholder `{tools}` in `base.yaml`, replaced in `Instance.get_system_prompt` by the list generated from the tool registry (see [`tools.md`](tools.md)). Nothing to update by hand when a tool changes.

## Tool schemas

There is no `config/tools.json` any more. The schemas are generated from the `@tool` functions at import time (module variable `assistant.tools`). See [`tools.md`](tools.md).

## Chat commands

Registered in `client.py` for every chat. Each runs only the first matching handler (`/command` matches before the catch-all message handler).

| Command | Arguments | Effect |
|---|---|---|
| `/help` | none | List all commands with a one-line description (`main.HELP_TEXT`, keep it in sync when adding a command; a test checks it). |
| `/schizo` | `[<personality>]` | Switch persona (base plus the persona; an unknown name changes nothing). No argument: shows the chat's current persona and lists every key in `index.yaml`, changing nothing. |
| `/stfu` | none | Toggle always-on. When off, messages are recorded but not answered. Adds an `API: Always On is now enabled/disabled...` user message to the context and replies `Always On is now Enabled/Disabled`. |
| `/model` | `[<model>[-<effort>]]` | See [`models-and-openrouter.md`](models-and-openrouter.md). |
| `/autoroute` | none | Toggle auto routing. |
| `/memory` | none | List this chat's memories with their ids (newest 30 at most, within 3000 characters; says how many older ones are not shown). |
| `/forget` | `<id>` | Delete one memory of this chat. A missing or non-numeric id gives a usage message; an id that is not this chat's gives "no memory N in this chat". |
| `/usage` | none | This chat's spend today (with the daily cap and what is left), over the last 7 days and in total. Only ever this chat's numbers. See [`usage-and-cap.md`](usage-and-cap.md). |

An unregistered command such as `/aura` (removed) or `/stfu@some_other_bot` is not matched by a command handler and falls through to the **message** handler, so the bot answers it as ordinary text (verified). `/usage` is planned (#31) and does not exist.

Command replies are written by `main.py` and wrapped in underscores (Telegram italic Markdown).

## Files created at runtime

| Path | Created by | Contents |
|---|---|---|
| `app.log` | `main.py` logging | All log output, including every user message and bot reply (INFO) and one `llm_call` line per LLM call. Not rotated. Gitignored. The Telegram bot token is redacted (`<redacted>`) from every line by `src/logsafe.py`; lines written before that change still contain it. Message content is not redacted. |

| `data/rotbot.db` | startup (`db.init()`) | SQLite database: the `reminders` and `memories` tables. Gitignored (`data`). Deleting it deletes every pending reminder and all memory. |

`data/` and `app.log` are gitignored. A `data/memory.json` left from before memory moved to SQLite is ignored.
