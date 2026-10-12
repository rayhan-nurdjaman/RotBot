# Contributing: setup, running, testing, changing code safely

## Requirements

- **Python 3.12** (the code relies on 3.12 f-string syntax).
- Linux or macOS shell examples below. The repo itself is OS-independent.
- Always work from the **repository root**. Config and data paths are relative (`config/...`, `data/...`) and the code fails with `FileNotFoundError: config/config.yaml` from anywhere else (verified).

## Setup

```sh
python3 -m venv env                       # `env/` is gitignored
env/bin/pip install -r requirements-dev.txt    # runtime deps + pytest + pyflakes
cp .env.example .env                      # then fill in TELEGRAM_API_KEY and OPENROUTER_API_KEY
```

Set a **hard spend limit on the OpenRouter key** in the OpenRouter dashboard. The program has a daily cap per chat (`daily_cap_usd`), but it can be overshot by one turn and the bot cannot protect the key itself.

## Running

```sh
env/bin/python src/main.py
```

Logs go to stdout and `app.log`. Stop with Ctrl-C. A bot token must exist (create one with Telegram's BotFather). Nothing here has been run against real Telegram yet; see [`verification-status.md`](verification-status.md).

## Tests

```sh
env/bin/python -m pytest -q               # 278 tests, about 2 seconds, no network
env/bin/python -m pyflakes src tests      # expect exactly 2 pre-existing warnings in src/
```

Layout (`tests/`):

| File | Covers |
|---|---|
| `conftest.py` | Puts `src/` on `sys.path`; sets dummy `TELEGRAM_API_KEY` and `OPENROUTER_API_KEY` |
| `test_assistant.py` | The agent loop: parallel tool grouping and order, step cap, finish reasons, error mapping, tool timeouts, malformed arguments, locking |
| `test_instance.py` | Trimming: no orphaned tool messages, chunked trimming |
| `test_cache.py` | Request ordering, breakpoint placement, tool list stability, `session_id`, `tool_choice` |
| `test_models.py` | `parse_model`, the request body, `/model`, `/autoroute`, routing, "request never ends with an assistant message" |
| `test_typing_and_reasoning.py` | Typing task lifecycle, reasoning kept within a loop and dropped between turns |
| `test_logging.py` | The `llm_call` line, image-only message logging, redaction of the bot token from log lines and tracebacks |
| `test_registry.py` | Registry: schema generation, strict mode, validation, result shape, prompt section |
| `test_memory.py` | The memory store (limits, duplicates, secrets filter, search ranking, per-chat scoping, persistence), the memory message and its caps, once-per-turn loading, the three tools, `/memory` and `/forget` |
| `test_reminders.py` | Reminder store and scheduler (fake clock, temp database): ordering, wake-up, late-on-startup, retry and `failed`, restart, cancel; the three tools (ISO parsing, offsets, DST, past times, chat scoping); `on_reminder` delivery and the lock |
| `test_clock.py` | Timezone, `stamp()`, message timestamps |
| `test_db.py` | `db.init`, migrations, failed migration, newer database refused |
| `test_usage.py` | Usage rows, per-chat totals, the daily cap and its midnight reset, the cap in `get_response` and reminders, `/usage` |
| `test_client.py` | Stickers, photo failures and largest-size selection in `client.on_message`; message splitting and the plain-text retry in `send_message` |
| `test_access.py` | The lists (parsing, bad entries, empty, fail closed), the refusal to start, and the gate: real `telegram.Update` objects through PTB's own dispatcher with Telegram's network blocked, strangers reach no handler, an allowed user does |
| `test_bash.py` | The `bash` tool with real commands, and that it does nothing outside `OP_CHAT_IDS`: output, exit code, stderr, working directory, no stdin, truncation, binary output, timeout kills child processes |
| `test_images.py` | `encode_image` (JPEG passthrough, long-edge cap, no enlarging, PNG with transparency) and that no `detail` field is stored |
| `test_main.py` | `get_instance`, personalities (new chat is `rotbot`, switching leaves nothing behind, a failed `/schizo` or a missing file changes nothing), always-on stub |

### How tests avoid the network and real state

- **No test calls Telegram or OpenRouter.** LLM calls are replaced by fakes (`monkeypatch.setattr(Assistant.ChatCompletion, 'create', fake)`) or the module's `openai_client` is replaced with a stand-in. Fakes **must match the real signature** `create(messages, model, tools=None, chat_id=None, tool_choice=None)`; changing the signature breaks every fake, which is intended.
- **Memory is in the database, so tests that save or search memory open a temp database** (`db.init(tmp_path / 'rotbot.db')`, `db.close()` afterwards). `Instance` itself never touches the database until `load_memory()` runs at the start of `get_response`; if no database is open, the error is logged and the turn goes on. Tests that run `get_response` without a database therefore work and see no memory.
- **`main.instances` is a module-level dict** shared between tests. Tests reset it with `monkeypatch.setattr(main, 'instances', {})`. Tests that need the database call `db.init(tmp_path)` and `db.close()` themselves, and control time with `monkeypatch.setattr(clock, 'now', ...)`.
- **`client.application`** is built at import with a dummy token. Replace the whole `application` attribute (or `Client.send_message` etc.) rather than assigning to `application.bot.<method>`, which PTB forbids.
- Test code uses `asyncio.run(...)` per test; there is no pytest-asyncio.
- Some tests derive expectations from the config (`Assistant.max_tool_steps`, `config['context_trim_fraction']`), so they follow value changes in `config/config.yaml`.
- Indentation in tests is 4 spaces; in `src/` it is **tabs**. Files use LF line endings (`.gitattributes` enforces it).

## Rules for changes that can silently cost money or break things

Read [`concepts.md`](concepts.md) rules 1 to 11 first. In short:

1. Do not put anything that changes per request into the system prompt, the tool list, or the history, and **do not add anything after the last history message** (it disables the cache when server tools are present: [`caching-and-server-tools.md`](caching-and-server-tools.md)). Time is stamped into user messages when stored. After a change that touches request assembly, **check `cached_tokens` on a real second request** (see below); no unit test can tell you the provider cache hits.
2. Do not send `temperature`, forced `tool_choice`, or end a request with an assistant message.
3. Do not edit `context_window` messages in place except through the `Instance` methods; stored breakpoints and half-written tool rounds break the API.
4. A tool must never raise out of a turn and must finish within `tool_timeout_s`.
5. Do not add a model without checking its entry in `https://openrouter.ai/api/v1/models` (`reasoning.mandatory`, `supported_efforts`).

## Verifying against the real API (when you change request code)

This costs real money (cents). Use a throwaway key limit.

```sh
env/bin/python - <<'EOF'
import sys, asyncio, os, logging
sys.path.insert(0, 'src')
from dotenv import load_dotenv; load_dotenv('.env')
os.environ.setdefault('TELEGRAM_API_KEY', '1:x')        # client is not used here, but other imports may need it
logging.basicConfig(level=logging.INFO)
import chat_completion as CC
from models import parse_model
msgs = [{'role': 'user', 'content': [{'type': 'text', 'text': 'Say hi in three words.'}]}]
asyncio.run(CC.create(msgs, parse_model('haiku-5.5-low'), chat_id=1))   # prints an llm_call line
EOF
```

The `llm_call` line shows tokens, `cached_tokens` and `cost`. To test caching, send the same long prefix twice (use a unique first line to avoid hitting an earlier entry) and confirm the second `cached_tokens` is close to the first `prompt_tokens`. See [`context-and-caching.md`](context-and-caching.md) for what was measured.

## Style and conventions in this repo

- `src/` uses **tabs**, short comments that explain *why*, and the callback pattern in [`architecture.md`](architecture.md). Match the surrounding file; do not reformat code you are not changing.
- Module aliases are capitalised (`import client as Client`).
- Error handling: tools return a dict (wrapped as `{"ok": true, ...}`) or raise `ToolError` (becomes `{"ok": false, "error": ...}`); the loop never lets a tool exception escape; API errors map to short user messages in `assistant.py`.
- New behaviour needs a test with fakes. New request-level behaviour also needs a note in these docs and, ideally, a live check recorded in [`verification-status.md`](verification-status.md).
- Commit messages: a short imperative subject, then a body explaining why; reference the issue (#N). Work happens on the `overhaul` branch.

## Keeping these docs true

When you change behaviour, update the relevant page in the same commit. The "Documentation was written at commit" line in [`README.md`](README.md) records how fresh the docs are; update it when you do a full pass. If you find a doc claim that no longer matches the code, trust the code and fix the doc.

## Project management

Issues and milestones are on GitHub (`rayhan-nurdjaman/RotBot`), tracked by issue #30. Milestone 0 (stabilise): its original seven issues are done, two bugs found later (#32, #37) are still open in it, and #34 is fixed. Milestone 1 (OpenRouter) is mostly done, Milestone 2 (tools) is done: the registry, server tools, reminders and memory. MCP (#17) was moved to Milestone 3 on purpose (researched, not started, not a priority: [`mcp.md`](mcp.md)). Milestone 3 (architecture) is not started. SQLite persistence (#18) is partly done: reminders and memories are in the database, conversations and per-chat settings are not.
