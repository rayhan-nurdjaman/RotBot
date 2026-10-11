from datetime import datetime, timezone
from typing import Annotated

from pydantic import Field

import clock as Clock
import memories as Memories
import reminders as Reminders
from tools.registry import registry, tool, ToolError

# Importing this module registers the tools. Other modules import the registry from here so that it is filled.
__all__ = ['registry']

def _local_iso(unix_time):
	return datetime.fromtimestamp(unix_time, timezone.utc).astimezone(Clock.zone).isoformat(timespec='minutes')

# Web search and page fetching run on OpenRouter, not here. The model calls them and OpenRouter executes them
# inside the same request. The caps below bound what one message can spend (the tools are paid per call).
registry.server_tool(
	'openrouter:web_search',
	'Searches the web for current information and returns result excerpts with their urls. Cite the sources you use.',
	engine='exa', max_results=3, max_characters=1500, max_uses=3,
)
registry.server_tool(
	'openrouter:web_fetch',
	'Reads the page at a url (use it after searching, or when the user gives a link) and returns its text. Long pages are cut off.',
	max_uses=3, max_content_tokens=10000,
)

# The docstring of each tool is sent to the model twice: as the tool description, and in the "Tools" section
# of the system prompt. Put when to use it and how it combines with other tools in there.

@tool
async def save_memory(ctx, text: Annotated[str, Field(description='One complete sentence that makes sense on its own. Include the username of the person it is about, e.g. "alice prefers tea over coffee".')]):
	"""Saves something to this chat's memory, so it is remembered in later conversations. Memory is shared by everyone in the chat.
	Use it for lasting facts and preferences (hobbies, likes, projects) and for standing instructions about how to reply in this chat (a tone, a style, a language).
	Never save passwords, keys, card or ID numbers, or anything else sensitive. Do not save small talk or things that will soon be outdated.
	Saved memories are listed at the start of the conversation. Do not save one twice; to change one, forget_memory the old one and save the new one."""
	try:
		memory_id, created = await Memories.add(ctx.chat_id, text)
	except Memories.Rejected as e:
		raise ToolError(str(e))
	return {'id': memory_id, 'text': text, 'already_saved': not created}

@tool
async def recall_memory(ctx, query: Annotated[str, Field(description='Words to look for, e.g. "tea coffee". An empty string returns the newest memories.')]):
	"""Searches this chat's saved memories. The newest ones are already listed at the start of the conversation, so use this when the user asks what you remember, or when you need something that is not listed there (older memories are noted as hidden).
	Returns each match with its id."""
	return {'memories': await Memories.search(ctx.chat_id, query)}

@tool
async def forget_memory(ctx, id: Annotated[int, Field(description='The id of the memory, shown in brackets in the memory list or returned by recall_memory.')]):
	"""Deletes one saved memory of this chat, for example when the user asks you to forget something or a fact is no longer true."""
	if not await Memories.delete(ctx.chat_id, id):
		raise ToolError(f'There is no memory with id {id} in this chat.')
	return {'forgotten': id}

@tool
async def set_reminder(
	ctx,
	description: Annotated[str, Field(description='What to remind about, written so it makes sense when it is read later. Include the username of the requester.')],
	when: Annotated[str, Field(description='When the reminder is due, as an ISO 8601 date and time, e.g. 2026-10-12T09:30:00. Without a UTC offset it is read in the chat timezone; with one (e.g. 2026-10-12T09:30:00+08:00) the offset is used.')],
):
	"""Sets a reminder. At the due time you are asked to remind the user, who is tagged by username.
	Work out the date and time from the current time given in the last message of the conversation. The time must be in the future.
	Use list_reminders to see what is already set, and cancel_reminder to remove one."""
	try:
		moment = datetime.fromisoformat(when.strip())
	except ValueError:
		raise ToolError(f'\'{when}\' is not an ISO 8601 date and time. Use a form like 2026-10-12T09:30:00.')
	if moment.tzinfo is None:
		moment = moment.replace(tzinfo=Clock.zone)
	due_at = int(moment.timestamp())
	if due_at <= int(Clock.now().timestamp()):
		raise ToolError(f'{moment:%Y-%m-%d %H:%M} is not in the future. It is now {Clock.stamp()} ({Clock.zone.key}).')
	reminder_id = await Reminders.add(ctx.chat_id, description, due_at)
	return {'id': reminder_id, 'description': description, 'when': _local_iso(due_at)}

@tool
async def list_reminders(ctx):
	"""Lists the reminders of this chat that are still waiting, soonest first, each with its id and due time.
	Use it before cancelling a reminder, or when the user asks what is set."""
	reminders = await Reminders.pending(ctx.chat_id)
	return {'reminders': [{'id': r['id'], 'description': r['description'], 'when': _local_iso(r['due_at'])} for r in reminders]}

@tool
async def cancel_reminder(ctx, id: Annotated[int, Field(description='The id of the reminder, from list_reminders or set_reminder.')]):
	"""Cancels a waiting reminder of this chat. Call list_reminders first if you do not know the id."""
	if not await Reminders.cancel(ctx.chat_id, id):
		raise ToolError(f'There is no waiting reminder with id {id} in this chat.')
	return {'cancelled': id}
