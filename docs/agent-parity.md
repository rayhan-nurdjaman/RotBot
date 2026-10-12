# Agent parity: what the Zed coding agent can do, and what RotBot needs to match it

Written 2026-10-12 by the Zed coding agent that was building RotBot with the owner. It is the reference for issue #51 ("Tool parity"). Use it to decide what to build next, and to check a finished feature against its requirement.

## How to read this page

- **"Me"** means the Zed agent as it was configured in the session that wrote this: the tool list below, taken from the tool definitions that session was given, plus the behaviour rules in its system prompt. Both are **verified by reading them**, not recalled from general knowledge. Zed lets the user switch tools off, so the list is a snapshot, not a fixed product.
- **"RotBot today"** was checked against the code and `docs/tools.md` on the same date.
- **Parity means capability, not identical names or UI.** A Telegram bot has no editor, no diff view and no language server, so some things are replaced or left out on purpose (see "Not applicable").
- **Requirement IDs** (`F1`, `X2`, ...) are stable, so issues and commits can cite them. A requirement says what must be true, in a way that can be tested. It does not say how to build it. Open design choices are listed separately and are the owner's to decide.
- Where an existing issue covers a requirement it is linked. "None" means no issue covers it yet.
- #51 also lists things taken from Claude Code that **I do not have** (background processes, a todo tool). They are marked "beyond me" so they are not mistaken for parity.

## 1. What the Zed agent can do

### Tools (16)

| Group | Tool | What it does, and the limits that shape its use |
|---|---|---|
| **Read** | `read_file` | Reads a file by project-relative path. Optional `start_line` and `end_line`. A **large file returns an outline of symbols with line numbers instead of the content**, and the instructions say to read ranges, not to retry. Can return **images** (PNG, JPEG, WebP, GIF, BMP, TIFF) as something I can look at. |
| | `list_directory` | Lists files and folders in one directory. |
| | `find_path` | Finds paths by glob (`**/*.rs`). Sorted, **50 results per page**, `offset` for the next page. |
| | `grep` | Regex search of file contents (Rust regex syntax). Optional `include_pattern` glob and `case_sensitive`. **20 matches per page**, `offset` for more. Each match carries the surrounding symbol so I can see where it is. |
| **Write** | `write_file` | Creates a file or overwrites it completely. The instructions tell me to check the folder exists first. |
| | `edit_file` | Applies a list of `old_text` to `new_text` replacements in order. The match is **fuzzy about whitespace**. Told to read the file first. |
| | `create_directory`, `copy_path`, `move_path`, `delete_path` | Folder creation (with parents), copy and move or rename (directories recursively), delete (directories recursively). |
| **Run** | `terminal` | Runs a shell one-liner and returns combined output. Required `cd` (a project root or a folder inside one). **Each call is a new shell, so no state carries over.** Optional `timeout_ms`, and `head_lines` and `tail_lines` that limit what I receive while the user still sees everything. Rules in the definition: no shell substitutions, no commands that never end (servers, watchers), no input (it is a pty, so anything that waits for input hangs), and paging and editor flags for git. No background processes. |
| **Web** | `fetch` | Fetches a URL and returns Markdown. Network access is **per host and must be granted**; the grant is shared with `terminal`, and every redirect hop needs one too. |
| **Check** | `diagnostics` | Errors and warnings from the language server, for the whole project (counts) or one file (every item). Said to be refreshed first, and it can be stale if the server does not support pull diagnostics. |
| **Delegate** | `spawn_agent` | Starts a sub-agent for a self-contained task and **returns only its last message**. The sub-agent **sees none of my conversation**, so the prompt must carry all context. Several can run in parallel if their write sets do not overlap. A returned `session_id` lets me send a short follow-up to the same agent. Optional `model`. |
| | `list_agents_and_models` | Lists agents and models available for `spawn_agent`. |
| **Instructions** | `skill` | Returns the full text of a named skill. The system prompt lists only each skill's **name, description and location**. Paths inside a skill resolve relative to its folder. |

### Behaviour that is not a tool

- **Paths and scope.** File tools take paths that start with a project root name. They cannot reach other places, except `~/.agents/skills`. Only `terminal` can leave the project (and its network use is permissioned per host).
- **Parallel tool calls.** I am told to issue independent calls in one step and to wait for dependent ones.
- **Long tasks.** One request can run many tool calls over many minutes with no fixed small step cap that I know of (the real limit is not visible to me; *not known*). The user sees each call as it happens, and can stop me.
- **Context.** A long conversation is **compacted** into a handoff summary that replaces the old messages. That is how this project kept going across hundreds of messages. The thread is saved, so work continues across sessions. I have **no memory tool** of my own.
- **Attachments.** The user can attach skills, files and other context to a message, and it arrives in the conversation as given.
- **Asking.** To ask a question or get approval I **end my turn** and the user replies. Nothing waits inside a tool.
- **Rules from the system prompt.** Gather facts before acting; make small, surgical changes; run the project's tests and report honestly what was and was not verified; do not commit unless asked; keep going until the task is done; send a short preamble before a group of tool calls; end with a short summary and the files changed. Format is Markdown with Mermaid diagrams, rendered by the editor.
- **No permissions of my own.** I run as the editor's user. The protection is the editor's approval prompts, the host grants for the network and the optional terminal sandbox, not anything in the agent.

## 2. What RotBot has today

Checked against `docs/tools.md`, `docs/agent-loop.md` and the code.

| Area | RotBot today |
|---|---|
| Tools | `save_memory`, `recall_memory`, `forget_memory`, `set_reminder`, `list_reminders`, `cancel_reminder`, `bash`, plus OpenRouter's `web_search` and `web_fetch` (3 uses each per turn). All registered through `src/tools/registry.py`; schema, validation and results are generated. |
| `bash` | One argument. Runs as the bot's user in the bot's **working directory**, with the bot's environment (keys included), **no sandbox** (owner's decision). Hard limit **20 s**, no input, output **cut at 8,000 characters** (the first 8,000, with a count of what was omitted). Only in chats listed in `OP_CHAT_IDS`. Every call is a fresh `bash -c`. |
| Files | None. Only `bash`. |
| Agent loop | At most **8 tool steps** per turn (9 model calls). Parallel tool calls are run together. Each tool is cut off at **30 s**. Updates are processed **one at a time**, so a long turn blocks every other chat. The user sees only the typing indicator until the final reply. |
| Context | A window of 8 to 24 **messages** per chat, trimmed in chunks. No summary, no token budget (#22). Tool results stay in the window as they are. Not saved across restarts (#18). |
| Incoming media | Photos (resized to 1568 px). **Documents, voice, video, stickers are ignored.** |
| Outgoing | Text only in practice (split at 4096, Markdown with a plain-text retry). `send_photo` and `send_animation` exist but nothing uses them. No file sending. |
| Safety | Allow-list of user ids (#35 done), `bash` only in op chats, daily spend cap per chat, `RedactingFormatter` on logs. No approval before a command (#43). |
| Instructions | A persona YAML per chat. No skills (#47), no project rules file (#53). |
| Delegation | None (#48). |

### Where RotBot already goes beyond me

Persistent reminders that fire on their own, per-chat memory the model can write and search, personas, many chats and users at once with an allow-list, a per-chat spend cap and `/usage`, automatic model choice (`/autoroute`, [`auto-routing.md`](auto-routing.md)), and server-side web search with citations. I have none of these as tools. They stay.

## 3. Requirements for parity

Status key: **have** (in RotBot), **partly**, **missing**. "Beyond me" means I do not have it either.

### Files and search

| ID | Requirement (testable) | Me | RotBot | Issue |
|---|---|---|---|---|
| **F1** | `read_file`: a path inside the workspace, optional first and last line. A file over a size limit returns a **bounded** result that says how to read the rest (a line range or an outline), never an unbounded dump. | `read_file` | missing | #51 |
| **F1a** | `read_file` on an image returns something the model can look at, within the 1568 px policy of `encode_image`. **Untested:** whether OpenRouter accepts an image part inside a `tool` message (`mcp.md` lists it as not tried). Fallback if not: the tool stores the image and the loop attaches it as a user message. | `read_file` | missing | #51 |
| **F2** | `write_file`: create or overwrite a file inside the workspace. Fails with a clear error if the folder does not exist, or creates it (an explicit choice, see decisions). | `write_file` | missing | #51 |
| **F3** | `edit_file`: a list of `old` and `new` pairs; each `old` must match **exactly once**; all pairs are checked before anything is written; a failure leaves the file untouched and names the pair. | `edit_file` (applies in order, matches fuzzily) | missing | #44 |
| **F4** | Folder tools: list a directory, create a directory (with parents), move or rename, copy, delete (a directory only with an explicit recursive intent). | `list_directory`, `create_directory`, `move_path`, `copy_path`, `delete_path` | missing | #51 |
| **F5** | Every file tool is confined to a configured **workspace root**. A path that escapes it (`..`, absolute, symlink) is refused. The bot's own database, `.env`, `config/` and `app.log` are not reachable by file tools. | by design | missing | #51 (constraint) |
| **S1** | `glob`: find paths by pattern under the workspace, paged, with a stated page size and a way to get the next page. | `find_path` (50 per page) | missing | #51 |
| **S2** | `grep`: regex over file contents, optional include glob and case flag, paged, each hit with path and line number and a little context. | `grep` (20 per page) | missing | #51 |
| **S3** | Every list-returning tool states when there is more and how to get it (`offset`). Output size is always bounded. | all of mine | `bash` only | #51 (constraint) |

### Running commands

| ID | Requirement | Me | RotBot | Issue |
|---|---|---|---|---|
| **X1** | `bash` takes a **working directory** argument, validated to be inside the workspace, instead of always running in the bot's own directory. | `terminal` (`cd`) | missing | #51 |
| **X2** | `bash` takes a **timeout** argument up to a configured maximum, and the 30 s tool timeout in `assistant.py` is aware of it (today the tool and loop limits are 20 s and 30 s). | `timeout_ms` | fixed 20 s | #51 |
| **X3** | The caller can ask for the **head or the tail** of the output. Today only the first 8,000 characters come back, which hides the end of a build or test log, the part that matters. | `head_lines`, `tail_lines` | head only | none |
| **X4** | State between calls is documented and deliberate. A fresh shell per call is what I have, and it is acceptable if the model is told. If instead state persists, working directory and environment must be defined. | fresh shell per call | fresh shell per call | #51 lists "kept between calls" |
| **X5** | **Background processes** with output polling and kill. | not available | missing | #51. **Beyond me.** |
| **X6** | A command can be refused before it runs, and a refused command tells the model so. | editor approval prompts | missing | #43 |

### Web

| ID | Requirement | Me | RotBot | Issue |
|---|---|---|---|---|
| **W1** | Fetch a URL and get readable text, bounded. | `fetch` | **have** (`web_fetch`, 10k token cap, 3 uses) | done |
| **W2** | Web search with sources. | none (I use `fetch` on search pages) | **have** (`web_search`) | done |
| **W3** | Network use is controllable per host. | host grants | none | none. Not required for parity; listed so it is a conscious omission. |

### Delegation, instructions, checking

**D1 to D4 are deferred by the owner** (decision 7); they stay listed so the requirement is not lost.

| ID | Requirement | Me | RotBot | Issue |
|---|---|---|---|---|
| **D1** | A tool that hands a self-contained task to a sub-agent in a **fresh context**, and returns only its final text. It gets a **subset** of tools and **cannot call the delegation tool** itself. It has its own step cap and timeout; an error comes back as a tool result, not a failed turn. Its spend counts against the chat's cap. | `spawn_agent` | missing | #48, #28 |
| **D2** | Several delegations in one round can run in parallel. | yes | the loop already gathers tool calls; the sub-agent and the chat lock must not conflict | #48 |
| **D3** | A follow-up to an earlier sub-agent without repeating the task (a `session_id`). | yes | missing | #48 (not mentioned; decide) |
| **D4** | Choose a cheaper model for the sub-agent. | `model` argument | `ModelSpec` exists | #48, #11 |
| **K1** | **Skills**: a folder with `SKILL.md`; the prompt lists only names and descriptions; a tool loads the body, and files inside the folder, by relative path. Loading must not change the cached prefix. | `skill` | missing | #47 |
| **K2** | A **project instructions file** that is loaded into every conversation, and pointers from the bot to its own code. | rules and skills in the system prompt | missing | #53, #47 |
| **K3** | The model can **check its work**: run the project's tests and read the diagnostics. For RotBot this is `bash` (tests and linters) and, optionally, compiler output. Language-server diagnostics are not required. | `diagnostics`, `terminal` | possible via `bash` | #51 (optional) |

### Conversation and interaction

| ID | Requirement | Me | RotBot | Issue |
|---|---|---|---|---|
| **C1** | **Long tasks.** A single request can use as many tool steps as the owner configures (per chat or persona), not a fixed 8, and the user can see progress while it runs. | many calls, visible live | 8 steps, typing indicator only | #21 (events), none for the cap |
| **C2** | **Stop.** The user can cancel a turn that is running (and its running commands). | the editor's stop button | **missing**: `/stfu` toggles always-on and does not cancel | none |
| **C3** | **Ask and wait.** The model can ask a question or request approval and continue after the reply. My way is to end the turn and let the reply start the next one. This needs the reply to be recognised as the answer, and the first half of the work to still be in context. | end turn | possible by ending the turn; no buttons | #43 (options A, B), #51 |
| **C4** | **Summarised history.** When the window is cut, older turns are folded into a summary, so long work does not lose its beginning. | compaction | missing: older messages are dropped | #22 |
| **C5** | **History survives a restart.** | saved threads | missing (memory and reminders are saved, conversations are not) | #18 |
| **C6** | **Receive files.** A document sent to the bot (text, PDF, code) reaches the model, within size limits. Today documents are ignored. | user attachments | missing | none |
| **C7** | **Send files.** The bot can send a file it made (and an image) to the chat, instead of pasting it. | not applicable in an editor | missing (`send_photo` exists, unused) | #51 ("send files as attachments") |
| **C8** | **Honest account of tool use.** The model's description of what it did matches what ran. | rules in the system prompt | broken for server tools | #52, #41 |
| **C9** | **Plan or todo list** for multi-step work. | not available | missing | #51. **Beyond me.** |

### Safety and governance

| ID | Requirement | Me | RotBot | Issue |
|---|---|---|---|---|
| **G1** | File and shell tools only run for people and chats the owner enabled. | editor user only | **have** for `bash` (`OP_CHAT_IDS`); to extend to file tools | #35 |
| **G2** | Anything that writes, deletes or executes can be approved or refused, with exactly what will run shown first. | editor prompts | missing | #43 |
| **G3** | Text the model reads from files and the web is data, not instructions. | system prompt rule | in the `bash` docstring only; extend to every new tool | #51 |
| **G4** | Every new tool has tests, a docs entry, and appends to the tool list without reordering it. | n/a | convention | #51, `tools.md` |
| **G5** | Tool use stays inside the spend cap, including sub-agents. | n/a | cap exists; sub-agent spend not counted yet | #48 |

## 4. Not applicable (deliberately not matched)

- **Editor UI:** diff viewer, inline edit review, multi-buffer views, the file tree and @-mentions of editor tabs. On Telegram, a change is reported as a short summary and, if needed, a file.
- **Language-server features** other than plain `diagnostics`: go to definition, rename symbol, and so on.
- **Streaming of edits.** Streaming replies are #27 and not part of parity.
- **Tool grants per host (W3).** Revisit if untrusted people ever get the shell.

## 5. Decisions

### Decided by the owner (2026-10-12)

1. **Workspace root (F5): the home folder (`~`).** The bot runs on Termux or a spare ThinkPad, not a VPS. **Consequence the owner should know:** the repository itself, `.env`, `data/rotbot.db` and `app.log` are *inside* the home folder, so a root of `~` does not keep file tools away from them. F5 therefore needs an explicit **deny list** inside the root (the repo's `.env`, `data/`, `app.log`, `config/`, and `~/.ssh` and similar). This is a guard against the model's mistakes and against prompt injection steering a file tool. **It is not a security boundary:** `bash` is unsandboxed in the same chats and can `cat .env` whatever the file tools refuse. Say so in `tools.md` when the tools are built, so nobody relies on it. The root is a setting (default `~`), not a constant, so it can be narrowed later. `~` on Termux and on Linux are different folders; resolve it at startup.
2. **Who gets file tools (G1): the op chats only (`OP_CHAT_IDS`).** Any tool that can destroy data or run code (`bash`, `write_file`, `edit_file`, folder move, copy and delete) is op-only. The read-only tools (`read_file`, `list_directory`, `glob`, `grep`) are not destructive, but they can read secrets, so they stay op-only as well unless the owner says otherwise. Same list as `bash`, one switch.
3. **Approval is shown as buttons in the chat (X6, G2, C3).** The command is sent as a message with buttons under it. Telegram calls these *inline keyboard* buttons; they are different from the bot's command menu. They are available in the installed library (python-telegram-bot 21.10: `InlineKeyboardMarkup`, `InlineKeyboardButton`, `CallbackQueryHandler`; checked 2026-10-12) and a press arrives as a normal update, so it already goes through the access gate in `client.gate`. The button data is limited to **64 bytes** (checked: a 65-byte value is rejected), so a button carries an id, and the command is stored on our side and shown from that stored copy. Updates are handled one at a time, so a turn cannot wait inside the tool for the press: this is **#43 option A** (end the turn, continue when the button is pressed). The full design is in section 5a.

4. **`edit_file` matches exactly (F3).** Each `old` must match once, byte for byte. When nothing matches or several places do, the error shows the closest lines (with line numbers) so the model can correct itself, and the file is left unchanged. **Is that how mine works? No.** My `edit_file` is documented to use *fuzzy* matching for small whitespace and formatting differences. It saves retries, and it is the reason I check the result afterwards: a fuzzy match can land on the wrong place and I would not be told. The owner's reasoning is the right one for a bot that edits files unattended.
5. **Skills reuse the `SKILL.md` format (K1).** A `name` and a `description` in front matter, then the body in Markdown; extra files are read by a path relative to the folder. Skills written for the Zed agent then work unchanged. Closes the first open question in #47.

### 5a. Approval design (settled 2026-10-12, except where marked open)

This is the design that #43 and the build order refer to. It is a design, not code: nothing here exists yet.

**Settled by the owner**

| Point | Decision |
|---|---|
| Who may press | **Any allowed user in the chat** (anyone who passes `client.gate`; the chat is already an op chat). Not only the person who asked. |
| Buttons | **Three:** *Approve this time*, *Approve for the day*, *Deny this time*. |
| Expiry | **60 seconds** (a setting, not a constant; the owner called the number arbitrary). |
| Long commands | Cut **for display only**. The stored command is what runs, whole, and the message says it was cut. |
| Denied or expired | The model is **told it was denied** (or expired) and asked to respond. The command does not run. |
| A reminder going off during an approval | Not a bug in this design, see below. |

**How it works (option A)**

1. The model calls `bash`. If this chat has no "approved for the day" in force, the tool does **not** run the command. It stores the exact command with a new id and a deadline (now plus the expiry), sends the message with the three buttons, and **returns a result to the model** such as `{"awaiting_approval": true}`. A result must be returned: the API requires every tool call to have a matching result, so "return nothing" would leave an invalid history.
2. The model words a short reply ("I've asked for approval"), and **the turn ends normally**. The chat's lock is released and the history is consistent (tool call, result, reply).
3. When a button is pressed, the handler checks that the id exists, has not expired and was not already answered (a second press says so and does nothing). Then:
   - **Approve** (this time or for the day): runs the stored command and adds the output to the chat as an `API:` user message (the way a reminder is added, `main.on_reminder`), then runs a model turn so the model can continue.
   - **Deny**: adds `API: The user denied the command '<cut command>'. It did not run.` and runs a model turn.
4. A **timer** for each request does the same as Deny when the 60 seconds pass, with the text "was not approved in time". The buttons are removed (the message edited) on press and on expiry, so they cannot be used twice.
5. **Approve for the day** records that this chat is approved until local midnight in the configured timezone, and the command that was waiting runs now. **Open, see below.**

**Why a reminder during an approval is fine.** The lock is held only while a turn runs, and step 2 ends the turn before anything waits. A reminder that comes due while the buttons are showing takes the lock, runs its own turn, sends its message, and releases it, exactly as when nothing is pending. The two share the chat without touching each other.

The alternative the owner suggested, holding the lock for the whole wait and letting reminders queue behind it, was checked against the code and **would be harmful**: (a) reminders are delivered **one at a time** by `reminders.process_due`, so a reminder waiting for one chat's lock would delay every other chat's reminders for up to the expiry; (b) with updates handled one at a time, the press itself could not be processed while a turn held everything, which is the reason option A exists; (c) every message from the user in that chat would also queue, including the "yes". So the lock must **not** be held while waiting. No code needs to change for reminders.

**Things that follow from it (decide or accept before building)**

- **A restart forgets pending approvals** and any "approved for the day" (both kept in memory, *recommended*: re-asking is the safe failure, and nothing is lost that cannot be asked again). A press on an old message after a restart should answer "this request is no longer valid".
- **Between the request and the answer, the chat carries on.** Other messages, and reminders, are handled normally. When the answer arrives, the `API:` message lands at the end of the history, possibly after unrelated messages. The model sees the command in the text, so it can place it.
- **Several `bash` calls in one model step** (parallel tool calls) make several requests, each with its own buttons. Other tools called in the same step run at once, so the model can have partly acted before the approval. Accept, or allow one pending request per chat; not decided.
- **The press runs a model turn**, so the daily spend cap applies and `Usage.CapReached` must be handled the way `on_reminder` does (tell the user plainly).
- **The same buttons are reused** for every write tool (F2 to F4), with the command replaced by a short description of the change.

**Open, needs the owner**

- **What "approve for the day" covers.** The owner chose this button but not its reach. Interpretation 1: **every** command in this chat until midnight, which is a blanket approval that switches the safeguard off for the day. Interpretation 2: only this **exact command text** until midnight. Interpretation 3: this command *and* the other tools of the same kind. *Recommendation:* interpretation 2 as the default, because 1 removes the protection against a command steered by a web page or a photo, which is the reason approval exists (#43). Not built until chosen.
- Whether the expiry should be longer when the owner is away (it is a setting, so this is only a default).

### Deferred by the owner

6. **Turn budget (C1)** (step cap, timeout, concurrency). Revisit with #21. Until then the cap stays at 8 steps.
7. **Sub-agents (D1 to D4): native tool or `openrouter:subagent`.** Rarely used by the owner, so #48 is not scheduled.

## 6. Suggested order

Same spirit as #51, with the gaps found here added and the 2026-10-12 decisions applied. Each step is independently reviewable and ends with a check.

1. **Settle the one open point of the approval design** (what "approve for the day" covers, section 5a). Nothing is built.
2. **X3, X1, X2** (head and tail, working directory, timeout). Small changes to `bash`. Check: a test that a 12,000-character log returns its last lines when asked, and that a `cd` outside the root or into the deny list is refused.
3. **Approval buttons** for `bash` (X6, G2, option A of #43), since every later write tool reuses them. Check: tests with a stand-in bot for the message, the id and the stored command, a press by an allowed and by a not-allowed user, an expired request; then a manual try on real Telegram by the owner.
4. **S1, S2, F1, F4 read side** (`glob`, `grep`, `read_file`, `list_directory`), read-only. Check: temp-directory tests for the size bound, paging, the root and the deny list.
5. **F2, F3, F4 write side** (`write_file`, exact `edit_file`, create, move, copy, delete), all behind the buttons. Check: tests for exactly-once, all-or-nothing, nearest-line errors and no escape from the root.
6. **C2 stop** (and #21 events, which also settles C1).
7. **C6 and C7** (files in and out). Check: a PDF and a text file sent from the owner's Telegram.
8. **K1, K2** (skills, rules file; #47, #53), then **C4, C5** (summary and persistence; #22, #18), then MCP (#17). **D1** (sub-agents, #48) is deferred.
9. **C8** (#52) in parallel: it is a bug in what the bot says and needs no new tool.

## 7. Requirements that no issue covers yet

`X3` (tail of output), `C1` (turn budget, partly in #21), `C2` (stop a running turn), `C6` (receive documents), `D3` (follow up a sub-agent), `G3` (untrusted-text rule for every tool). They are listed on #51 as additions.

## Related

[`tools.md`](tools.md) (how tools are added), [`access-control.md`](access-control.md), [`agent-loop.md`](agent-loop.md), [`mcp.md`](mcp.md), [`known-issues.md`](known-issues.md). Issues: #51, #43, #44, #47, #48, #53, #21, #22, #18, #52, #35, #17, #11.
