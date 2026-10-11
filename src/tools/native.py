from typing import Annotated

from pydantic import Field

from tools import time_manager as TimeManager
from tools import search_engine as SearchEngine
from tools.registry import registry, tool, ToolError

# Importing this module registers the tools. Other modules import the registry from here so that it is filled.
__all__ = ['registry']

# The docstring of each tool is sent to the model twice: as the tool description, and in the "Tools" section
# of the system prompt. Put when to use it and how it combines with other tools in there.

@tool
async def save_to_memory(ctx, user_info: Annotated[str, Field(description='The information to store, provided as a complete sentence.')]):
	"""Stores non-sensitive user information (e.g., general preferences or hobbies) to enhance future responses and interactions."""
	return ctx.instance.add_memory(user_info)

@tool
async def web_search(ctx, search_term: Annotated[str, Field(description='The keyword or phrase used to search the web for relevant content.')]):
	"""Searches the web to find relevant websites when needed.
	Returns a list of websites and their info such as their title, url, and brief snippets."""
	results = await SearchEngine.get_links(search_term)
	if results == ['Unable to get results']:
		raise ToolError('Unable to get results.')
	return {'results': results}

@tool
async def get_url_text(ctx, url: Annotated[str, Field(description='The website url the AI will read and analyze from.')]):
	"""Reads the website at the given url and returns its text and outbound links.
	Use this after 'web_search' if further detail is needed. Can also be chained if the retrieved page has additional links."""
	result = await SearchEngine.get_text(url)
	if 'state' in result:
		raise ToolError(result['state'])
	return result

@tool
async def get_time(ctx):
	"""The ONLY source of reliable time. Returns the time in YYYY-MM-DD HH:MM:SS format.
	Useful for reminders or time-sensitive tasks."""
	return TimeManager.get_time()

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
	Always invoke 'get_time' first to ensure accuracy. Do not call this in parallel to 'get_time', wait for its response first."""
	result = TimeManager.add_reminder(ctx.chat_id, description, year, month, day, hour, minute, second)
	if 'description' not in result:
		raise ToolError(result['state'])
	return result
