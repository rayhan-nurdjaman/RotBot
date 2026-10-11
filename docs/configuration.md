# Configuration

Everything is read **once at import or instance creation** and not watched. After editing a config file, restart the program (and note that existing `Instance`s keep the values they already loaded; instances are lost on restart anyway).

All paths are relative to the repository root, which must be the working directory.

## Environment variables (`.env`)

Loaded by `load_dotenv()` in `main.py` before the modules that need them are imported. `.env` is gitignored. `.env.example` lists the two required ones.

| Variable | Required | Used by | If missing or wrong |
|---|---|---|---|
| `TELEGRAM_API_KEY` | **yes** | `client.py` (at import) | Import raises `telegram.error.InvalidToken`. A well-formed but wrong token starts, then fails with `InvalidToken: Unauthorized`; the extra `RuntimeError: This Updater is not running!` after it is a side effect, ignore it. |
| `OPENROUTER_API_KEY` | **yes** | `chat_completion.py` (at import) | Unset: import raises `OpenAIError`. Empty or wrong: every LLM call fails (HTTP 401) and users see "Something went wrong with that request." |

`GOOGLE_API_KEY` and `GOOGLE_SEARCH_ENGINE_ID` are no longer used: web search runs on OpenRouter, billed to the same key. `OPENAI_API_KEY` is no longer read by anything in `src/` (only by retired code in `unused/`). `OPS` (an admin list for the removed `/sql` command) is also unused now.

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

`Instance.set_personality(key)` sets `self.personality = key`, then loads that key's file and overlays its values on the current ones.

`Instance.__init__` intends the default persona to be `rotbot`: it sets `self.personality = 'rotbot'`, calls `set_personality('base')`, then calls `set_personality(self.personality)`. But the first call has already overwritten `self.personality` with `'base'`, so the second call loads `base` again. **Verified: a new instance runs as `base`** (no custom instruction, `context_length` 8), not as `rotbot` (4,085-character custom instruction, `context_length` 24). Run `/schizo rotbot` to get the rotbot persona. See [known-issues.md](known-issues.md) item 2.

`/schizo <name>` (via `main.set_instance_personality`) loads `base` first, then the requested persona, so that traits from a previous persona do not stack. It does **not** clear the conversation history. Earlier messages written in the old persona stay in the context and can influence the new one (few-shot contamination; the README TODO mentions compaction as a future idea). On an unknown name it replies `The personality '<name>' does not exist.` but, because the function sets `instance.personality` and loads `base` before it looks the name up, **the chat has by then been reset to base and its `personality` attribute holds the unknown name** (verified; see known-issues item 2).

The system prompt text itself contains: identity and role (RotBot, username `rotrot_botbot`, replies in group chats formatted as `<username>: <message>`), brevity and Markdown guidance, a source-citing format, a capabilities list (vision at 512 by 512), a `# Tools` section, a `# Time` section (explains the `[date time]` stamp on each user message, with the `{timezone}` placeholder filled from `config.yaml`), and a memory paragraph pointing to the `API: Memory` message that follows the system prompt. The `# Tools` section is the placeholder `{tools}` in `base.yaml`, replaced in `Instance.get_system_prompt` by the list generated from the tool registry (see [`tools.md`](tools.md)). Nothing to update by hand when a tool changes.

## Tool schemas

There is no `config/tools.json` any more. The schemas are generated from the `@tool` functions at import time (module variable `assistant.tools`). See [`tools.md`](tools.md).

## Chat commands

Registered in `client.py` for every chat. Each runs only the first matching handler (`/command` matches before the catch-all message handler).

| Command | Arguments | Effect |
|---|---|---|
| `/schizo` | `<personality>` | Switch persona (resets to base, then applies). No argument: `Usage: /schizo <personality>`. |
| `/stfu` | none | Toggle always-on. When off, messages are recorded but not answered. Adds an `API: Always On is now enabled/disabled...` user message to the context and replies `Always On is now Enabled/Disabled`. |
| `/model` | `[<model>[-<effort>]]` | See [`models-and-openrouter.md`](models-and-openrouter.md). |
| `/autoroute` | none | Toggle auto routing. |
| `/memory` | none | List this chat's memories with their ids (newest 30 at most, within 3000 characters; says how many older ones are not shown). |
| `/forget` | `<id>` | Delete one memory of this chat. A missing or non-numeric id gives a usage message; an id that is not this chat's gives "no memory N in this chat". |

An unregistered command such as `/aura` (removed) or `/stfu@some_other_bot` is not matched by a command handler and falls through to the **message** handler, so the bot answers it as ordinary text (verified). `/usage` is planned (#31) and does not exist.

Command replies are written by `main.py` and wrapped in underscores (Telegram italic Markdown).

## Files created at runtime

| Path | Created by | Contents |
|---|---|---|
| `app.log` | `main.py` logging | All log output, including every user message and bot reply (INFO) and one `llm_call` line per LLM call. Not rotated. Gitignored. |

| `data/rotbot.db` | startup (`db.init()`) | SQLite database: the `reminders` and `memories` tables. Gitignored (`data`). Deleting it deletes every pending reminder and all memory. |

`data/` and `app.log` are gitignored. A `data/memory.json` left from before memory moved to SQLite is ignored.
