# MCP (Model Context Protocol): research and plan

**Status: not implemented, and not a priority right now.** This page records what was found while researching issue #17 so the work can be picked up later without redoing it. Nothing here is in the code. Everything marked **Verified** was run on 2026-10-11 against `mcp` 2.3.0 with a small test server written with that SDK, in a scratch directory outside the repository. The scratch scripts were not kept; the results are in the tables below.

## What MCP is, in this project's terms

An MCP **server** is a separate program that offers tools (and other things, see "Out of scope"). The bot would be the MCP **client**: it starts or connects to servers, asks each for its tool list, shows those tools to the model next to our own tools, and when the model calls one, forwards the call to the server and hands the result back. OpenRouter does not host MCP for Python. Its own page on MCP shows this same client-side pattern. So MCP support is code in this repository, and the tool registry (`src/tools/registry.py`) is where the tools would be added.

Why do it at all: it lets the bot use tools somebody else already wrote (calendar, email, notes, file search) by editing a config file instead of writing a Python function each time.

## The SDK: use `mcp` 2.x, not what OpenRouter's example uses

`pip index` shows `mcp` 2.3.0 as current (1.30.0 is the last 1.x). OpenRouter's example targets the old 1.x API and does not work on 2.x. Differences that matter:

- Tool fields are snake_case in 2.x (`tool.input_schema`, `result.is_error`); the example reads `tool.inputSchema`.
- The entry point is `mcp.Client(...)` used as `async with`. A `Client` cannot be reused after the block ends, and there is no `connect()` / `close()` pair.
- `Client` takes `StdioServerParameters` (start a local program), a URL string (Streamable HTTP), or a server object (in-process, for tests).
- A 2.x timeout is a plain number of seconds, not a `timedelta`.
- **The example's converter is wrong on its own terms.** It reads `tool.inputSchema["required"]`, but a tool with no required arguments has **no `required` key** (**Verified**: the test server's `no_args` tool reports `{"type": "object", "properties": {}, "title": ...}`), so the example raises `KeyError` on it. It also copies only `properties` and `required`, dropping everything else, and it executes only the first tool call of a response. Do not copy it.

**Dependencies.** A dry run (`pip install --dry-run mcp==2.3.0` in the project's `env`, nothing installed) resolves with no conflict and would add 19 packages: `mcp`, `mcp-types`, `httpx2`, `httpcore2`, `starlette`, `uvicorn`, `sse-starlette`, `cryptography`, `PyJWT`, `opentelemetry-api`, `jsonschema`, `jsonschema-specifications`, `referencing`, `rpds-py`, `click`, `python-multipart`, `truststore`, `cffi`, `pycparser`. I did not find a client-only install, so the server-side packages come along (not checked in depth). It needs `pydantic>=2.12` (the project pins 2.14.0) and Python 3.10 or newer. It brings `httpx2` instead of `httpx`; the `openai` package still needs its own `httpx`, and the migration guide says the two install side by side (not tried here). The SDK is new and had breaking changes between 1.x and 2.x, so pin the exact version (`mcp==2.3.0`), as the other requirements are.

## What was verified (stdio transport, local test server)

| Question | Result |
|---|---|
| Does `Client(StdioServerParameters(...))` connect and list tools? | Yes. It negotiated protocol `2026-07-28` by default (`mode='auto'`, which falls back to the older handshake for older servers). |
| What does a tool schema look like? | JSON Schema with extra `title` keys on the object and on each property, `default` on optional arguments, and **no `required` key** when nothing is required. |
| Can MCP tools use `strict: true` like our own tools? | **No.** Strict mode needs every property in `required`, `additionalProperties: false` and no defaults. MCP schemas generally break all three. MCP tools must be sent non-strict. |
| Does the model call non-strict tools correctly next to our strict ones? | **Yes, live on Haiku** (one request, under a cent): a request with all our tools plus two MCP-shaped tools made two correct calls, `spike__add {"a": 5, "b": 7}` and `spike__no_args {}`. The `spike__` prefix was added by the test script. |
| How do tool failures arrive? | As an ordinary result with `is_error=True` and the text in `content`, not as an exception. That includes a tool that raised, an unknown tool name, and arguments that fail the server's validation (the message includes pydantic's text, which the model can read and retry on). |
| What happens on a timeout? | `read_timeout_seconds` on the call raises `MCPError` with code `-32001` ("Request 'tools/call' timed out after 1.0s"). The connection stays usable: the next call worked. |
| Are parallel calls on one connection really parallel? | Yes. Four 1-second calls through `asyncio.gather` took 1.0 s. This matches how `assistant._process_tool_call` runs a response's tool calls. |
| What happens when the server program is missing? | `FileNotFoundError` at connect, immediately. |
| What happens when it exits right away? | An `ExceptionGroup` ("unhandled errors in a TaskGroup") at connect, immediately. Catch `Exception` and unwrap if the message matters. |
| What happens when it starts but never answers? | The connect hangs. With an 8 s limit around it, control came back after 10.0 s (cleanup of the process took about 2 s). We must put our own timeout on connecting. |
| What happens when the server dies during a call? | That call raises `MCPError` code `-32000` "Connection closed", immediately. Every later call on that `Client` raises the same. **The client does not reconnect by itself.** Leaving the `async with` afterwards was clean. |
| Does a reconnect design work? | **Yes, one shape did.** One long-lived task (the "owner") enters the `Client` and waits on an event; other tasks only call `client.call_tool`. A caller that sees `-32000` sets the event; the owner leaves the `async with`, waits a backoff delay, and enters a new one. Parallel calls from other tasks worked, calls while down got "server unavailable", calls after reconnect worked, cancelling the owner shut down cleanly, and no server process was left behind. A simpler "connect on first use, drop and reconnect on `-32000`" version, where the calling task itself enters and leaves the `Client`, also worked, but only with sequential calls from one task. With parallel calls it was not tried, and I would expect trouble, because anyio wants a context exited by the task that entered it (*inferred*). A first supervisor attempt that polled the connection for liveness did not reconnect (my script's fault, not investigated). |
| What does the server program see of our environment? | Only a small default set: `HOME`, `LC_CTYPE`, `LOGNAME`, `PATH`, `SHELL`, `TERM`, `USER`. With dummy `OPENROUTER_API_KEY` and `TELEGRAM_API_KEY` set in the parent, **neither reached the server**. Passing `env={...}` to `StdioServerParameters` adds variables on top of that default. Secrets a server needs must be passed explicitly. |
| What can a result contain? | A list of content blocks: text, image (`ImageContent` with `mime_type`), and others. A tool that returns nothing gives an empty `content` list and `structured_content == {'result': None}`. Our stored tool messages are JSON strings, so something has to flatten blocks to text (see decision 5). |

## What was not verified

- **Any third-party server.** This machine has no `node`, `npx`, `uv` or `uvx`, and most published MCP servers are started through one of those. Nothing was run against a real server such as a filesystem, calendar or search server.
- **The HTTP transport** (`Client("https://...")`), including passing a static `Authorization` header (it needs a pre-built `httpx2` client) and OAuth. The SDK documents both; neither was tried.
- **Whether cancelling a call mid-flight reaches the server.** The 2.x migration guide says cancelling the awaiting task sends `notifications/cancelled`; `assistant` already cancels tool calls after `tool_timeout_s` (30 s), so this would matter. Not tried.
- **Image results sent back to the model.** Whether OpenRouter accepts image parts in a `tool` message was not tried.
- **Tool name rules.** I believe Anthropic limits function names to letters, digits, `_` and `-` and 64 characters (*inferred*, not checked in this project), which matters for the `server__tool` prefix below.
- Windows, and servers that write to stdout themselves.

## Where it would plug in, and what has to change first

Today `src/tools/registry.py` builds tools at import time from decorated Python functions with strict schemas, and `assistant.py` does `tools = registry.schemas()` **once, at import** (line 19). The system prompt's Tools section is built from the registry on every request (`Instance.get_system_prompt`). Both must stay identical between requests or the prompt cache misses (see [`context-and-caching.md`](context-and-caching.md)). MCP tools only exist after a server has been started and asked, so:

1. The registry needs a way to add a tool at runtime from `(name, description, json_schema, async handler)` without Pydantic model generation and without `strict`.
2. All servers must be connected and their tools added **before the first request**, and then the list must be **frozen** for the life of the process. `assistant.tools` must be computed after that point, not at import.
3. The `{tools}` section of the system prompt is generated from each tool's description. For MCP tools that description is **text written by the server's author, put into the cached system prompt**. See "Trust" below.

## Proposed design (not approved)

- **New file `src/tools/mcp.py`**: reads `config/mcp.yaml` (a new file; if it is missing or has no servers, nothing changes and `mcp` is not even imported), connects at startup, registers tools, and owns the reconnect task described above.
- **Config shape** (one entry per server):
  ```yaml
  servers:
    notes:                       # becomes the tool prefix
      command: python            # stdio server: program and arguments
      args: [examples/mcp_echo_server.py]
      env: {}                    # extra variables for that process (secrets go here, not in the repo)
      tools: [add]               # allowlist; only these are registered
      chats: []                  # empty = every chat; otherwise only these chat ids may call it
      call_timeout_s: 30
      max_result_chars: 8000
  ```
- **Names**: `<server>__<tool>` (for example `notes__add`) so two servers cannot collide with each other or with a native tool. The registry already rejects duplicate names.
- **Schemas**: take the server's `input_schema`, drop the `title` keys, send it non-strict. Arguments are validated by the server (it returned a readable error in the test).
- **Calls**: one handler per tool calls `client.call_tool(name, arguments, read_timeout_seconds=...)`. `is_error` results become `{'ok': False, 'error': text}`; `MCPError -32001` becomes a timeout message; `-32000` becomes "unavailable" and tells the owner task to reconnect. A call that died with the connection is **not retried**: it may already have done something, and repeating a side effect (sending a message, creating an event) is worse than reporting a failure.
- **Results**: join the text blocks; replace non-text blocks with a short placeholder such as `[image omitted]`; cut at `max_result_chars` and say so in the text. The cut matters: a single 92,000-character page once cost about 70,000 tokens (#36), and tool messages stay in the history until trimmed.
- **Startup failure**: a server that cannot start or does not answer within a timeout is logged at CRITICAL, its tools are **absent for this run** (adding them later would change the tool list and invalidate the cache once), and the bot starts normally. Fixing it takes a restart.
- **Failure mid-run**: tools stay listed; calls return "unavailable" until the owner task has reconnected.
- **Tests**: the SDK can connect a `Client` to a server object in-process, so most of this can be tested without subprocesses; the reconnect behaviour needs one real subprocess test with a server that exits on demand.
- **Sample server**: a 20-line server in the repository (an `add` tool, and one that fails) so the feature can be tried without installing anything else. It uses the same `mcp` package.

### Trust (read this before enabling any server)

- **Tool output is untrusted text.** A server, or a web page or document a server reads, can return text that tells the model what to do ("ignore your instructions..."). That text lands in the history like any tool result. The model is not a security boundary.
- **Tool descriptions are untrusted text too, and they would sit in the system prompt.** A compromised or malicious server controls them. Options in decision 4.
- **The tools are reachable from every chat.** The tool list is global, because a per-chat list would break the cache. Today anyone who finds the bot can talk to it (#35, no access control). If a server is wired to a private account (mail, calendar, files), any chat could ask the model to use it. **Do not enable a server with private data or write access until access control exists (#35) or the server has a `chats:` list.** The `chats:` check would run when the tool is called, so it does not change the tool list or the cache; the model still sees the tool in every chat and gets a "not available in this chat" result.
- **No server with filesystem or shell access should be enabled for a group chat.** (From the issue; still true.)

## Decisions for you

Each has a recommendation; none is made.

1. **Dependency weight.** `mcp` adds 19 packages (above) even though only the client is used. *Recommendation:* add it to `requirements.txt` pinned (`mcp==2.3.0`) and import it only when `config/mcp.yaml` lists a server. The alternative is a separate `requirements-mcp.txt`.
2. **Transports.** *Recommendation:* stdio first. Remote servers (HTTP) usually need OAuth or a token, which is a separate chunk of work and untested here.
3. **Allowlist default.** *Recommendation:* a server's tools are registered only if listed under `tools:`, so a server update cannot silently add a new tool (for example one that deletes things) to your bot.
4. **Descriptions in the system prompt.** (a) Use the server's description as written, which is how native tools work; (b) put only the tool name and a short description you write in the config; (c) keep MCP tools out of the prompt's Tools section entirely and rely on the `tools` parameter, which already carries each description. *Recommendation:* (c); the model gets the description through the `tools` parameter either way, and the cached system prompt stays free of third-party text. It needs a small change to `prompt_section()`.
5. **Non-text results.** *Recommendation:* text only for now, with an `[image omitted]` placeholder. Images need a check of what OpenRouter accepts in tool messages first.
6. **Gating by chat.** *Recommendation:* implement the `chats:` list in the first version, since it is a few lines and is the only protection until #35 is done.
7. **Priority and milestone.** Decided: not a priority, and #17 now lives in Milestone 3 (Milestone 2 is closed out). *Recommendation:* do access control (#35) before it.

## Draft steps (when it becomes a priority)

```
1. Registry: add runtime tools (external schema, non-strict, no Pydantic model), a freeze(), and move
   assistant.tools out of import time
   → verify: existing 198 tests still pass; new tests for adding after import, duplicate names, and a frozen
     registry refusing additions; prompt prefix test still passes
2. src/tools/mcp.py with an in-process server for tests: config loading, namespacing, allowlist, schema
   cleanup, result flattening and truncation, is_error mapping, per-chat gating
   → verify: unit tests with an in-process Client(server)
3. Subprocess handling: connect timeout, startup failure, owner task with reconnect and backoff, shutdown
   → verify: a test with a real subprocess server that exits on demand; no leftover processes
4. Wire into main.main() before Client.init, and the sample config + sample server
   → verify: start the bot with the sample config (Telegram faked), live Haiku turn calls the sample tool,
     second request shows cached_tokens high (the #41 recipe)
5. Docs: update tools.md, configuration.md, architecture.md, known-issues.md (the trust notes), this page
```

## Out of scope for a first version

- MCP **resources** and **prompts** (the SDK supports them; the bot has nowhere to show them).
- **Sampling, elicitation and roots**: the server asking the client to call the model, ask the user, or list folders. Sampling and roots are deprecated in the 2026-07-28 revision, and on that revision server-to-client requests are refused outright (`NoBackChannelError`), which is what `Client` negotiates by default.
- **OAuth** for remote servers.
- **`tools/list_changed` notifications.** The tool list is fixed for the session on purpose.
- Per-chat tool lists (cache).

## References

- OpenRouter: "Using MCP Servers with OpenRouter" (the example this page corrects).
- MCP Python SDK 2.x: <https://py.sdk.modelcontextprotocol.io/> (client page, and the v1 to v2 migration guide).
- GitHub issue #17 (the original checklist), #35 (access control), #36 and #41 (reasons for the result cap and for the frozen tool list).
