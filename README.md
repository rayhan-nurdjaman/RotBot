# RotBot

A Telegram chatbot with per-chat personalities, memory, reminders and tools. Replies come from Claude 5.5 (Haiku, Sonnet, Opus) through OpenRouter.

This project consists of a couple of modules
- Client: This takes care of everything frontend, currently its telegram.
- Assistant: This takes care of everything backend, currently Claude 5.5 through OpenRouter.

# Documentation
Full documentation for humans and agents is in [`docs/`](docs/README.md). Start with [`docs/concepts.md`](docs/concepts.md) and [`docs/architecture.md`](docs/architecture.md); known bugs are in [`docs/known-issues.md`](docs/known-issues.md).

# Setup
Copy `.env.example` to `.env` and fill in `TELEGRAM_API_KEY`, `OPENROUTER_API_KEY` and `ALLOWED_USER_IDS` (numeric Telegram user ids of the people who may use the bot; the bot will not start without it). Set `OP_CHAT_IDS` only for chats where the model may run shell commands. Also set a hard spend limit on the OpenRouter key in the OpenRouter dashboard, as a safety net the bot cannot bypass.

# Models
`/model <model>[-<effort>]` picks the model and reasoning effort together for a chat, e.g. `/model haiku-5.5-high`. Models: `haiku-5.5`, `sonnet-5.5`, `opus-5.5`. Efforts: `low`, `medium`, `high`, `xhigh`, `max`. The default is the cheapest, `haiku-5.5-low`; with no effort given it uses `low`. `/model` alone shows the current one.

`/autoroute` toggles auto routing (off by default). When on, it overrides `/model` and `router.route()` picks the model each turn. That function is currently a stub that always returns the cheapest model.

# Memory
Each chat has a shared memory of short facts and standing instructions (for example "reply in a formal tone"), kept in the SQLite database. The model saves, searches and forgets memories with tools. `/memory` lists them with their ids and `/forget <id>` removes one. Anyone in a chat can use both.

# Prompt caching
Requests are ordered so the front of the prompt stays identical between requests, which lets Anthropic's prompt cache hit: static system prompt and tool list first, then an append-only history with a cache breakpoint on its last message, with the chat's memory as a message right after the system prompt (it changes only when a memory is saved or removed). Nothing may come after the history. History is trimmed in chunks (`context_trim_fraction` in `config/config.yaml`), so the window grows by that fraction of `context_length` before being cut back, instead of shifting on every message. Personality switches change the system prompt and so reset the cache, intentionally. `cached_tokens` is in the `llm_call` log line. Prompts under 512 tokens are not cached by the 5.5 models.

# Privacy
Every message the bot can see in a chat, including group messages and photos, is sent to a third-party model provider (OpenRouter, which forwards it to Anthropic) to generate replies. Message text and replies are also written to `app.log` by the application logger.

# TODO
- Remove any mention of chat logging or data collection, not important right now.
- casino.py also deprecated, move to a seperate folder for now and revisit later and develop the api interface instead
- Research open router api
- Figure out any good search tool providers/mcp/whatnot
- make a calendar tool etc and avoid the current poll pattern in use
- disable always on, it is not applicable anymore
  - if not: ~~replace the current model with jev via open router~~ (done for model routing, see docs/auto-routing.md; the always-on decision itself is still a stub)
  - as fallback, always reply 5 mins (stored in a variable) after the last message has been sent to support multiple texts. consider vector style time increments (30 sec -> 1 min -> 2 min) for every consecutive message, though realistically assumptions dont justify that method
- current architecture is bad, need to change everything
- when changing personalities, consider compacting previous prompts and starting fresh with only the system prompt and the compacted context to avoid accidentally few shot prompting the model
- improve memory or make it a blackboard kinda thing. when the user sends an image for an event or a text snippet with no other context, based on a memo created like "any random snippet of dates or events the user sends must be added as a calendar reminder, always follow up to the user if details are vague"
- google calendar api integration maybe?
