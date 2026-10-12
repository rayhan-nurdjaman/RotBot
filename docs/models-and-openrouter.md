# Models, effort, routing and the OpenRouter request

## Why OpenRouter and the `openai` package

All model calls go to OpenRouter (`https://openrouter.ai/api/v1`), which exposes an OpenAI-compatible Chat Completions endpoint for Anthropic models. The code uses the `openai` Python package (`openai==1.59.7`) pointed at that URL. Decisions made with the owner: keep the `openai` SDK (the `openrouter` SDK was judged not worth it), keep **Chat Completions** (not OpenRouter's Anthropic-style Messages API), and pin model slugs.

OpenRouter-only fields (`reasoning`, `session_id`, and cache controls on message parts) are passed through `extra_body` or inside messages, because the pinned SDK version has no named parameter for them. Response fields the SDK does not model (`reasoning_details`) are read from `message.model_extra`.

The Messages API was not adopted. It documents extra Claude controls (for example a `between_tools` thinking mode and `clear_at` ephemeral system messages) that this project does not use. If those are ever needed, revisit.

## The model catalogue (`src/models.py`)

| Name | OpenRouter slug | Input / output price per 1M tokens (as listed 2026-10-11) | Reasoning |
|---|---|---|---|
| `haiku-5.5` | `anthropic/claude-haiku-5.5` | $0.10 / $0.50 | optional, default effort `medium` |
| `sonnet-5.5` | `anthropic/claude-sonnet-5.5` | $2 / $10 | **mandatory**, default `high` |
| `opus-5.5` | `anthropic/claude-opus-5.5` | $4 / $20 | **mandatory**, default `high` |

All three have a 1,000,000-token context and accept efforts `low`, `medium`, `high`, `xhigh`, `max` (from OpenRouter's public `/models` list). Prices are copied from that list and can change; the authoritative cost per request is the `cost` in each `llm_call` log line.

Slugs are **pinned** on purpose. OpenRouter's `~anthropic/claude-*-latest` aliases move to new versions without notice and new versions change behaviour (sampling restrictions, defaults). To support a new model, add it to `MODELS` in `src/models.py` and check its constraints in OpenRouter's `/api/v1/models` entry (`reasoning.mandatory`, `supported_efforts`).

Sonnet and Opus cannot have reasoning disabled (HTTP 400 if you try). Haiku can, but the code never sends `none`: it always sends one of the five efforts.

## `ModelSpec` and `/model`

A `ModelSpec` is `(name, effort)`. It prints as `name-effort` (`haiku-5.5-high`). Model and effort are one user-visible value on purpose: the owner wants them bound together, and a future router will pick both.

`parse_model(text)`:

- Lowercases and trims. Splits on the **last** hyphen.
- `haiku-5.5-high` parses to (`haiku-5.5`, `high`).
- `haiku-5.5` (no effort) parses to (`haiku-5.5`, `low`). The default effort is `low`, not the model's own default, because cost is the priority.
- Anything else (`gpt-4o`, `haiku`, `haiku-5.5-ultra`, `haiku-5.5 high`, an empty string) raises `ValueError`. There is no fuzzy matching.

**Defaults:** `DEFAULT_MODEL = haiku-5.5-low` (cheapest model, lowest effort). Every new `Instance` starts with it. Personalities never set a model.

### Chat commands

| Command | Effect |
|---|---|
| `/model` | Reports the chat's current model. |
| `/model <model>[-<effort>]` | Sets it for this chat only. Replies with the new value, plus a note if auto routing is on (it overrides `/model`). On a bad value it lists valid models and efforts and changes nothing. |
| `/autoroute` | Toggles auto routing for this chat (**off by default**). Replies with the new state. |

Anyone in a chat can run these (no admin check). The setting lasts until restart. See [`telegram-client.md`](telegram-client.md).

Precedence for a turn (`assistant._run_turn`): `if instance.auto_route: router.route(instance) else: instance.model`. Chosen **once per turn**.

### The router (`src/router.py`)

`route(instance) -> ModelSpec` is a **stub**: a `match` with one case that returns `DEFAULT_MODEL`. The owner plans to replace the body with a classifier that reads the conversation and picks the model. Contract for whoever does that: keep the signature (it receives the whole `Instance`), return a `ModelSpec`, do not mutate the instance, and expect it to be called once per turn **while holding the chat's lock** (a slow router delays that chat). No rules for images or long input exist.

`/model` is open to the same chats whose auto-route is off; there is no per-user permission model anywhere.

## The request (`chat_completion.create`)

`create(messages, model, tools=None, chat_id=None, tool_choice=None)` sends:

```json
{
  "model": "anthropic/claude-haiku-5.5",
  "messages": [ ... ],
  "max_tokens": 16000,
  "user": "<chat_id>",
  "tools": [ ... ],
  "parallel_tool_calls": true,
  "tool_choice": "none",
  "reasoning": {"effort": "low"},
  "session_id": "<chat_id>"
}
```

| Field | Value | Why |
|---|---|---|
| `model` | `ModelSpec.slug` | pinned slug |
| `max_tokens` | `config.max_tokens` (16000) | reasoning and the answer share it; too small and a reply can be empty (`finish_reason: length`) while still billed |
| `reasoning.effort` | from the spec | Sonnet and Opus think by default; effort is the cost and latency control. Always sent explicitly. |
| `session_id` | chat id as a string | sticky provider routing (helps caching) and groups requests in OpenRouter logs |
| `user` | chat id as a string | stable end-user id for OpenRouter logs |
| `tools`, `parallel_tool_calls` | only when `tools` is given | parallel tool calls are allowed |
| `tool_choice` | only `"none"`, only when `tools` is given | used for the forced final answer after the step cap |
| `temperature`, `top_p`, `top_k` | **never sent** | Claude 5.5 rejects non-default values |

Not sent: attribution headers (`HTTP-Referer`, `X-OpenRouter-Title`), `reasoning.max_tokens`, `reasoning.exclude`, `models` (fallback list), `provider` preferences. Per the issue text (OpenRouter's migration notes, not verified here), Haiku 5.5's tokenizer produces about 30 percent more tokens than Haiku 4.5; no token limits in this repo depend on it yet.

Messages are explicitly assembled by `Instance.get_context()`; see [`context-and-caching.md`](context-and-caching.md).

### The response

`create` returns `response.choices[0]` (an `openai` `Choice`): `.finish_reason`, `.message.content`, `.message.tool_calls`, and extras in `.message.model_extra` (`reasoning`, `reasoning_details`). Only the first choice is used. `message.content` is a string for these models (every observed response). The code does **not** handle `content` being a list of parts; if a model ever returns that, `_finish` would store it as text incorrectly. (Inferred, not observed.)

Observed usage object (Haiku 5.5): `prompt_tokens`, `completion_tokens`, `completion_tokens_details.reasoning_tokens`, `prompt_tokens_details.cached_tokens`, `prompt_tokens_details.cache_write_tokens`, and `cost` (dollars).

### Authentication

`OPENROUTER_API_KEY` is read from the environment when `chat_completion` is **imported**. If it is unset, the import raises `OpenAIError: The api_key client option must be set...` (verified). If it is set but wrong, the first request fails with 401, which users see as the generic `REQUEST_MESSAGE`; the real cause is in `app.log`. An empty string imports fine and fails on first use. `OPENAI_API_KEY` is **not** used anywhere in `src/`.

### Spend safety

The bot has **no spend cap**. A cap per chat and a `/usage` command are planned (#14, #31, after SQLite). Meanwhile the only limit is the one set on the key in the OpenRouter dashboard (the current key reports a $50 limit). Cost in practice: a Haiku 5.5 turn with a warm cache is about $0.0001 to $0.0003; Opus costs roughly 40 times more per token.

## Request failures

See [`agent-loop.md`](agent-loop.md). Not implemented: model fallback (`models: [...]`), per-model retry policy, circuit breaking, deadline per turn.
