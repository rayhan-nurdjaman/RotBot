from typing import Annotated

from pydantic import Field

from tools import time_manager as TimeManager
from tools.registry import registry, tool, ToolError

# Importing this module registers the tools. Other modules import the registry from here so that it is filled.
__all__ = ['registry']

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
async def save_to_memory(ctx, user_info: Annotated[str, Field(description='The information to store, provided as a complete sentence.')]):
	"""Stores non-sensitive user information (e.g., general preferences or hobbies) to enhance future responses and interactions."""
	return ctx.instance.add_memory(user_info)

@tool
async def set_reminder(
	ctx,
	description: Annotated[str, Field(description='A brief explanation of the purpose or content of the reminder. Make sure to include the username of the requester.')],
	year: Annotated[int, Field(description='The year for the reminder')],
	month: Annotated[int, Field(description='The month for the reminder (1-12)')],
	day: Annotated[int, Field(description='The day of the month for the reminder (1-31)')],
	hour: Annotated[int, Field(description='The hour for the reminder (0-23)')],
	minute: Annotated[int, Field(description='The minute for the reminder (0-59)')],
	second: Annotated[int, Field(description='The second for the reminder (0-59)')],
):
	"""Sets a reminder on the specified year, month, day, hour, minute, and second.
	Work out the date and time from the current time given in the last message of the conversation."""
	result = TimeManager.add_reminder(ctx.chat_id, description, year, month, day, hour, minute, second)
	if 'description' not in result:
		raise ToolError(result['state'])
	return result
