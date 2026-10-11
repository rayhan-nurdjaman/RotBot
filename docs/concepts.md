# Concepts and rules

Read this before changing anything. Every term below is used with exactly this meaning everywhere in the code and docs.

## Vocabulary

| Term | Meaning |
|---|---|
| **Instance** | One `Instance` object (`src/instance.py`) per Telegram chat. Holds that chat's conversation, personality, memory, model and flags. Created lazily by `main.get_instance(chat_id)` and kept in the in-memory dict `main.instances`. **Lost on restart.** The `Instance` id (`unique_id`) is the Telegram chat id. |
| **Chat** | A Telegram chat (private or group). One chat is one Instance. |
| **Context window** | `instance.context_window`: the stored list of conversation messages for a chat, in OpenAI Chat Completions format. Does **not** include the system prompt or the memory message. |
| **Context / request messages** | What is actually sent to the model: `instance.get_context()` = system prompt + context window (with one cache breakpoint added) + volatile messages. Built fresh on every call, never stored. |
| **Turn** | One call to `assistant.get_response(instance)`: from "a message needs a reply" to "a final text answer is produced". A turn contains one or more LLM calls. |
| **Tool round** | One LLM response that asks for tools, plus the tool results. Stored as one `assistant` message holding all `tool_calls`, followed by one `tool` message per call. |
| **Tool loop** | The sequence of tool rounds inside one turn. Capped at `max_tool_steps`. |
| **Model spec** | `models.ModelSpec(name, effort)`, for example `haiku-5.5` + `high`, written `haiku-5.5-high`. Model and reasoning effort are always chosen together. |
| **Slug** | The OpenRouter model id, for example `anthropic/claude-haiku-5.5`. Derived from the spec name. Never hardcode slugs outside `src/models.py`. |
| **Personality** | A YAML file in `config/personality/` that adds a `custom_instruction` on top of the `base` persona. Chosen per chat with `/schizo <name>`. A personality never chooses a model. |
| **Memory** | A list of short strings per chat, saved by the `save_to_memory` tool to `data/memory.json`, shown to the model as a trailing message. |
| **Reminder** | A `(time, description)` pair per chat, held in memory only, fired by a polling loop. |
| **Always-on** | A per-chat flag. When off, messages are recorded but the bot does not reply. `/stfu` toggles it. |
| **Auto routing** | A per-chat flag (`/autoroute`). When on, `router.route(instance)` picks the model spec each turn and `/model` is ignored. |
| **Cache breakpoint** | A `cache_control` marker on the last content part of the last history message. It tells the provider "cache everything up to here". |
| **Volatile data** | Anything that changes between requests (currently only memory). It must go last in the request, after the breakpoint. |
| **Reasoning details** | `reasoning_details`: structured thinking blocks returned by some models. They must be sent back unchanged within a tool loop. |

## Rules that are easy to break

Each rule says why. Do not break one without reading its reason.

### Message and API rules

1. **A request must end with a `user` or `tool` message, never an `assistant` message.** Claude 5.5 rejects assistant prefill with HTTP 400 ("This model does not support assistant message prefill"). Verified live. See [known-issues.md](known-issues.md) item 1 for a case where this is currently violated.
2. **Never send `temperature`, `top_p` or `top_k`.** Claude 5.5 rejects non-default sampling. `chat_completion.create` deliberately has no such parameter.
3. **Never force a tool with `tool_choice`** (`required` or a named function). Rejected on Sonnet and Opus 5.5. The only `tool_choice` the code uses is `'none'`.
4. **Never remove or reorder tools between requests.** The tool list is part of the cached prefix. To stop tool use, send `tool_choice='none'` and keep the list.
5. **Within one tool loop, `reasoning_details` must be sent back unchanged** on the assistant message that made the tool calls. Between turns they are dropped (`instance.drop_reasoning()`), because they are bound to the model that produced them and the model can change between turns.
6. **`max_tokens` is shared with reasoning.** If it is too small the model can spend it all thinking and return `finish_reason: "length"` with empty content, still billed. It is set to 16000 in `config/config.yaml`.
7. **Every `tool_call` must have exactly one `tool` message answering it, in order, immediately after it.** The provider rejects the request otherwise. `Instance.add_tool_round` writes the whole group at once for this reason. Never append tool messages one at a time from concurrent tasks.

### Context and cache rules

8. **The front of the request must be identical between requests**, or the prompt cache misses and every request is billed at full price. This is why history is trimmed in chunks, memory is a trailing message, and the system prompt never contains per-request data.
9. **Never put volatile data in the system prompt or the history.** Put it in `get_volatile_messages()`.
10. **Cache breakpoints are added to a copy at request time, never to the stored context.** Otherwise they pile up. Providers limit how many breakpoints a request may carry (Anthropic documents 4; not tested here).
11. **The stored context never starts with a `tool` message.** `_trim_context` drops leading tool messages after trimming, because their `tool_calls` message was cut off and providers reject orphans.

### Behaviour rules

12. **One turn at a time per chat.** `assistant.get_response` holds `instance.lock` for the whole turn. Without it, two quick messages interleave their tool rounds in one context.
13. **Tools never raise out of a turn.** `assistant._process_tool_call` catches everything and returns `{"ok": false, "error": ...}` to the model so it can recover. A tool that raises would otherwise kill the turn.
14. **A failed LLM call leaves the user's message in the context**, and nothing is stored for the failed reply, so the user can simply retry.
15. **Reasoning text is never sent to the chat.** Users only see the typing indicator.
16. **Messages from the model are not trusted as HTML or Markdown-safe.** `client.send_message` tries Markdown and falls back to plain text if Telegram rejects it.

## Message format

All messages use the OpenAI Chat Completions shape. OpenRouter accepts it unchanged. Exact shapes and examples are in [`context-and-caching.md`](context-and-caching.md).

## Where state lives

| State | Where | Survives restart? |
|---|---|---|
| Conversation (context window) | `Instance.context_window` | No |
| Per-chat model, auto-route flag, always-on flag, personality | `Instance` attributes | No |
| Memory | `data/memory.json` (key = chat id as a string) | **Yes** |
| Pending reminders | `tools/time_manager.reminders` dict | No |
| Logs | `app.log` (and stdout) | Yes |
| Usage and cost | Only the `llm_call` log lines | Only as log text |

Persisting everything else in SQLite is planned (#18). Until then, a restart wipes every conversation, setting and reminder.
