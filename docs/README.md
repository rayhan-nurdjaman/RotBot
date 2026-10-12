# RotBot documentation

RotBot is a Telegram chatbot. Each Telegram chat gets its own conversation, personality, memory and model. Replies come from Claude 5.5 models through OpenRouter. The model can call tools (web search, time, reminders, memory).

These docs describe the code **as it is now**, not as planned. Planned work lives in GitHub issues and is only referenced here when it changes how you should write code today.

**Documentation was last updated for commit `3d28d30` plus the reminder rewrite and the memory rewrite (#19) on branch `overhaul`.** If the code has moved on, trust the code and fix the docs.

## How to use these docs

If you are an agent or a new contributor, read in this order:

1. [`concepts.md`](concepts.md): vocabulary and the rules that are easy to get wrong. Read this first. It is short.
2. [`architecture.md`](architecture.md): what each file does, how a message flows through the program, and what runs concurrently.
3. The reference page for the area you are changing (table below).
4. [`known-issues.md`](known-issues.md): bugs and traps that exist today. Check it before you trust or build on a piece of behaviour.
5. [`contributing.md`](contributing.md): how to run, test and change the code without breaking things.

## Which page answers which question

| Question | Page |
|---|---|
| What does term X mean (instance, turn, tool round, tier, breakpoint)? | [`concepts.md`](concepts.md) |
| Which file does what? What happens when a user sends a message? | [`architecture.md`](architecture.md) |
| What are the exact message shapes sent to the model? How is context stored, trimmed and cached? | [`context-and-caching.md`](context-and-caching.md) |
| **Why can nothing come after the history? Why is the time stamped into messages?** (silent cache failure with server tools) | [`caching-and-server-tools.md`](caching-and-server-tools.md) |
| How does the agent loop work? What happens on errors, timeouts, odd finish reasons? | [`agent-loop.md`](agent-loop.md) |
| How do I add or change a tool? What tools exist and what do they return? | [`tools.md`](tools.md) |
| Who can use the bot? Where can the shell tool run? How do I add a friend? | [`access-control.md`](access-control.md) |
| How do models, efforts, `/model` and `/autoroute` work? What does the OpenRouter request look like? | [`models-and-openrouter.md`](models-and-openrouter.md) |
| What does config file X contain? What are the env vars, commands, personalities? | [`configuration.md`](configuration.md) |
| What is the Telegram client's behaviour, and what are its limits? | [`telegram-client.md`](telegram-client.md) |
| What is known about adding MCP servers (issue #17), and what is the plan? | [`mcp.md`](mcp.md) |
| What was verified against the real API and what was never run? | [`verification-status.md`](verification-status.md) |
| What is broken or surprising right now? | [`known-issues.md`](known-issues.md) |
| How do I set up, run, test, and avoid breaking the cache? | [`contributing.md`](contributing.md) |

## Conventions used in these docs

- **"Verified live"** means a real request was made to OpenRouter and the behaviour observed. **"Tested"** means a unit test with fakes covers it. **"Untested"** means neither. See [`verification-status.md`](verification-status.md).
- File and function names are in `code font` and relative to the repository root.
- Where a doc says **must** or **never**, breaking the rule causes a real failure (an API error, a lost cache, a crash). Reasons are always given next to the rule.
- Issue numbers like #13 refer to https://github.com/rayhan-nurdjaman/RotBot/issues.
