# Auto routing (Jev) and prompt caching

**Read this before changing `src/router.py`, the candidate models, the question text, or what the router sends to Jev.** It also records what is known about how switching models affects the prompt cache. The router was added on 2026-10-12 (owner's decisions, listed at the end).

## What it does

`/autoroute` turns auto routing on for one chat (off by default). While it is on, `assistant._run_turn` ignores `/model` and does `model = await router.route(instance)` **once per turn**; every step of the tool loop then uses that model.

`router.route(instance)` asks **Jev**, a decision model on OpenRouter, which of two models should answer:

| Candidate (a `ModelSpec` name) | Meant for |
|---|---|
| `haiku-5.5-low` (also the default model) | Casual chat, simple questions, short replies, simple reminders or memory requests, small lookups |
| `sonnet-5.5-medium` | Reading images or documents with small text, multi-step reasoning, planning, code, long or ambiguous requests, using several tools in sequence |

Opus is never chosen. Effort is part of the candidate: the router picks "model and effort" as one value, as with `/model`.

## What Jev is (and is not)

Jev is a **decision model** from TypeSafe, not a chat model. You send it some text (`state`) and a multiple-choice question; it returns the chosen option, a probability for every option and a confidence value. It generates no prose and no reasoning. A call costs about **$0.00002** and took **0.5 to 2.5 seconds** in the checks below (the first call of a process is the slowest). It is billed to the same OpenRouter key.

- **Endpoint used:** `POST https://openrouter.ai/api/v1/systemone`, model `typesafe/jev-1.13` (pinned, not the `~typesafe/jev-latest` alias, for the same reason slugs are pinned: a new version can answer differently). `POST https://openrouter.ai/api/alpha/decisions` returned the same answers; `v1/systemone` was chosen because "alpha" suggests it may change.
- **Why not `chat_completion.create` / the `openai` SDK:** Jev is not served on Chat Completions. `router.py` calls it with `httpx` directly (pinned in `requirements.txt`).
- **Why not `typesafe/jev-router`:** that slug exists on OpenRouter's model list and is a *chat-completions* router that picks from the whole model ecosystem. It cannot be limited to our two candidates, and (*inferred*, not tested) a router that can land on any model would also make cache behaviour harder to reason about. Not used.
- Sources read on 2026-10-12: OpenRouter's "Jev Documentation", "Jev Tutorial" and "TypeSafe SDK" pages (`https://openrouter.ai/docs/guides/community/jev.md`, `.../jev-tutorial.md`, `.../typesafe-sdk.md`). Jev's context length is 32,000 tokens, which is why images are never sent to it.

## The request

```json
{
  "model": "typesafe/jev-1.13",
  "state": "user: [2026-10-12 Mon 18:19] hey\nassistant: hi!\nuser: [2026-10-12 Mon 18:19] ok do that",
  "questions": {
    "model": {
      "type": "choice",
      "instructions": "Which model should answer the last user message in this conversation? Pick the cheapest model that can answer it well.",
      "criteria": { "haiku-5.5-low": "<description>", "sonnet-5.5-medium": "<description>" }
    }
  }
}
```

The response `answers.model.choice` is one of the criteria keys. The code turns it into a `ModelSpec` with `models.parse_model`.

**The descriptions in `CANDIDATES` and the `QUESTION` text are the routing policy.** Jev reads them. Changing a word changes which turns go to Sonnet. After editing them, re-run the live check below and look at the probabilities, not only the choice.

### What `state` contains (`router._state`)

- The **last 6 user and assistant messages** (`HISTORY_MESSAGES`), oldest first, one line each as `role: text`, **each cut to 1,000 characters** (`MAX_CHARS`).
- User messages keep the `[2026-10-12 Mon 18:19]` stamp that `Instance.add_user_message` writes. It is harmless noise for Jev.
- **Images are replaced by `[image attached]`.** Their base64 data would exceed Jev's 32k limit and cost money for nothing. This marker is the only signal that a photo was sent, and the live checks show it is enough to push a whiteboard question to Sonnet.
- **Tool calls, tool results, memory messages and the system prompt are not sent.** Jev does not see which tools exist or what a previous tool returned.
- The state is rebuilt from `instance.context_window` on every turn; nothing is cached or stored.

**For future changes:** if routing quality is poor, the state is the first suspect. Things deliberately left out that you might add: the persona name, whether the chat has memories, the number of tools used in the previous turn, whether the previous reply was long. Each one is a text line in `state`; none needs a new request shape. Keep it small: Jev is billed per input token and the call is on the critical path of every turn.

## Failure behaviour

`route` **never raises**. Any of these returns `DEFAULT_MODEL` (`haiku-5.5-low`), logs one WARNING `Jev routing failed (<ErrorType>: <message>). Using haiku-5.5-low.`, and lets the turn go on: timeout (`jev_timeout_s`, 5 seconds), connection error, any non-2xx status (a bad key gives 401, no credit gives 402), a body that is not JSON, a missing answer, a choice that `parse_model` rejects, or a valid model that is not one of the two candidates. The user sees nothing. A routing failure is invisible except in `app.log`, so a broken Jev call quietly pins an auto-routed chat to Haiku. Grep for `Jev routing failed`.

A failed or slow Jev call delays the turn by up to `jev_timeout_s` (the chat's lock is held meanwhile, like any other part of the turn). There is no retry.

## Logging and cost

Each successful call writes one line to `app.log`:

```
jev_call {"chat_id": "123", "choice": "sonnet-5.5-medium", "probabilities": {"haiku-5.5-low": 0.04, "sonnet-5.5-medium": 0.96}, "confidence": 0.92, "cost": 1.764e-05, "latency_s": 0.669}
```

Grep `jev_call` to see every decision. The probabilities are logged on purpose: the code takes Jev's `choice` as it is and ignores them, but if you later want a threshold (for example "Sonnet unless Jev is at least 90 percent sure it is simple"), the logged values are the data to tune it on. In the checks below, clear cases were 1.0/0.0 or 0.96/0.04, and the least clear case was 0.73/0.27 (confidence 0.46).

**Jev's cost is not written to the `usage` table**, so it does not count towards the daily cap or show in `/usage`. It is about 0.002 percent of a Haiku turn, so the understatement is negligible; the `jev_call` line is the only record. (Owner's decision. If you ever want it in `usage`, the table's `model` and `effort` columns are `NOT NULL`, so it would need a placeholder or a migration.)

## Routing versus the prompt cache

### What is known

Prompt caching is per model (see [`context-and-caching.md`](context-and-caching.md)). The worry was that a chat flipping between Haiku and Sonnet would pay the cache-write price on every flip. **Measured on 2026-10-12, the flips do not evict the cache.** One script sent the same ~18,500-token prompt (system message plus one user message with the breakpoint, one fixed `session_id`) through this sequence:

| # | Model | `cached_tokens` | Cost |
|---|---|---|---|
| 1 | haiku-5.5-low | 0 (cache write) | $0.00232 |
| 2 | haiku-5.5-low | 18,510 | $0.00019 |
| 3 | sonnet-5.5-medium | **0** (first time on this model: write) | **$0.04632** |
| 4 | sonnet-5.5-medium | 18,510 | $0.00190 |
| 5 | haiku-5.5-low | **18,510** (after Sonnet) | $0.00019 |
| 6 | sonnet-5.5-medium | **18,510** (after Haiku) | $0.00190 |
| 7 | haiku-5.5-**high** | 18,510 (different effort, same model) | $0.00019 |
| 8 | haiku-5.5-low | 18,510 | $0.00019 |
| 9 | haiku-5.5-high | 18,510 | $0.00019 |

What this says (all on the same 9-request run):

1. **Each model keeps its own cache entry.** Going back to Haiku after a Sonnet turn read Haiku's earlier cache. The cost of auto routing is therefore **one cache write per model per prefix**, not one per flip.
2. **The first request on a model that has not seen this prefix is a full write**, about 20 times the price of a read on Sonnet ($0.046 against $0.0019 for 18.5k tokens). Real chats are mostly 3k to 7k tokens, so a first Sonnet write is a few cents, not four.
3. **Changing the effort on the same model did not matter** (rows 7 to 9). This matches the existing `/model` behaviour.
4. After a **trim** (every few messages, see `context-and-caching.md`) the prefix changes for every model; each model that is used afterwards pays its own write again. Routing makes this happen up to twice per trim instead of once. This was not measured directly.

### What was NOT measured (do not assume)

- **Cache lifetime.** All nine requests were within about a minute. Anthropic's default cache lifetime is five minutes (documented by Anthropic, not re-checked here). A chat whose turns are further apart than that loses the cache on every turn regardless of routing.
- **The real request shape.** The script had no tool list, no memory message and no server tools. With `openrouter:web_search` in the list (`engine: exa`), caching was shown to be fragile ([`caching-and-server-tools.md`](caching-and-server-tools.md)). Whether per-model cache entries survive flips *with the full tool list* is **untested**. The same goes for tool rounds, images in the history and a real trim.
- **Provider stickiness.** `session_id` is meant to keep requests on one provider. Whether Haiku and Sonnet requests from one `session_id` both stay put was not examined; the results above suggest they did.
- **Whether Opus behaves the same.** It is not a routing candidate, and was not tested.
- **Real routing patterns.** The sequence above was chosen to stress the cache, not copied from a conversation. How often a real chat will flip is unknown until it is used. After real use, compare `cached_tokens` against `prompt_tokens` in `llm_call` lines grouped by `model`, and look at how often consecutive `jev_call` choices differ.

A formal test is tracked in GitHub issue #55 ("Auto routing vs prompt cache: formal test with the real request shape").

### Re-running the measurement

This is the script used. It spends about five cents, sends nothing to Telegram, and writes no database rows. Run it from the repository root with `env/bin/python`.

```python
import sys, os, asyncio, logging, uuid
sys.path.insert(0, 'src')
from dotenv import load_dotenv
load_dotenv('.env')
os.environ.setdefault('TELEGRAM_API_KEY', '123:dummy')   # importing the app modules needs it to be set
import chat_completion as CC
from models import parse_model
from instance import _with_cache_breakpoint

logging.disable(logging.CRITICAL)
nonce = uuid.uuid4().hex                                   # unique text, so no earlier run can have cached it
filler = ' '.join(f'Fact {n} about the project {nonce}: the committee meets on day {n % 28 + 1} to review item {n}.' for n in range(420))
messages = [
    {'role': 'system', 'content': 'You are a terse assistant. Reference notes: ' + filler},
    _with_cache_breakpoint({'role': 'user', 'content': [{'type': 'text', 'text': 'Reply with just the word ok.'}]}),
]
chat_id = 'cache-spike-' + nonce[:8]
steps = ['haiku-5.5-low', 'haiku-5.5-low', 'sonnet-5.5-medium', 'sonnet-5.5-medium', 'haiku-5.5-low',
         'sonnet-5.5-medium', 'haiku-5.5-high', 'haiku-5.5-low', 'haiku-5.5-high']

async def main():
    for name in steps:
        m = parse_model(name)
        r = await CC.openai_client.chat.completions.create(
            messages=messages, model=m.slug, max_tokens=4000,
            extra_body={'reasoning': {'effort': m.effort}, 'session_id': chat_id}, user=chat_id)
        u = r.usage
        cached = getattr(getattr(u, 'prompt_tokens_details', None), 'cached_tokens', None)
        print(f'{name:20} prompt={u.prompt_tokens:6} cached={cached!s:6} cost=${(getattr(u, "cost", None) or 0):.5f}')
asyncio.run(main())
```

To test the real shape, replace `messages` with `instance.get_context()` from a populated `Instance` and pass `tools=` the same list `assistant.py` builds.

## Live checks of the routing decision

2026-10-12, real OpenRouter key, Telegram not involved, six `route()` calls (about $0.0001 in total). Probabilities are `haiku / sonnet`:

| Conversation | Chosen | Probabilities |
|---|---|---|
| "lol that was funny" | haiku-5.5-low | 1.00 / 0.00 |
| "remind me to buy milk at 6pm" | haiku-5.5-low | 1.00 / 0.00 |
| "hey" / "hi!" / "ok do that" | haiku-5.5-low | 1.00 / 0.00 |
| (image) "what deadlines are on this whiteboard? set reminders for each" | sonnet-5.5-medium | 0.11 / 0.89 |
| a debugging request about asyncio deadlocks | sonnet-5.5-medium | 0.02 / 0.98 |
| "plan my next two weeks around exams on the 20th, 24th and a project due the 30th, and book study blocks" | sonnet-5.5-medium | 0.27 / 0.73 |

This is six hand-written cases, not an evaluation. In particular the whiteboard case is the one the owner cares about (reading small text on a photo and inferring reminder times), and it is the least decisive after the planning request. The image in this test was a placeholder marker, not a real photo; Jev never sees the pixels in any case, so **how hard the photo really is cannot influence the choice**, only the accompanying words.

## Tests

`tests/test_router.py` (no network, `httpx.MockTransport`): both answers, the exact request (URL, model, candidates), the state rules (last 6, 1,000-character cut, image marker, tools left out), the instance is not mutated, seven failure modes all falling back with a warning, the timeout comes from the config, the `jev_call` line, the auth header. `tests/test_models.py` covers that `_run_turn` uses the router only when auto routing is on and asks it once per turn.

## Decisions (owner, 2026-10-12)

1. The state is the last few messages as text with image markers (not only the last message, not the whole window); documented above so it can change.
2. Any Jev failure falls back to the cheapest model and logs a warning; the user is not told.
3. Jev's `choice` is used as is; probabilities are only logged.
4. Jev's cost is logged, not stored in `usage`.
5. Endpoint `v1/systemone`.
6. The question and candidate descriptions as written in `src/router.py`.

## Known limits

- Stickiness is not implemented: a chat can flip every turn. Given the measurement above this is cheaper than feared, but it is not proven for the real request shape.
- Only two fixed candidates; adding one means editing `CANDIDATES` (the name must parse with `parse_model`) and re-checking the descriptions against each other.
- The route is decided before the tool loop starts, so a turn that begins as chat and turns out to need many tools stays on Haiku. Mid-turn escalation is part of issue #11.
- The setting is not persisted across restarts (issue #18).
