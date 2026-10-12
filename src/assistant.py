import logging

import io
import json
import base64
import asyncio
import openai
from PIL import Image

from config import config
from tools.native import registry
from tools.registry import ToolContext
import client as Client
import chat_completion as ChatCompletion
import router as Router

logger = logging.getLogger(__name__)

tools = registry.schemas()

max_tool_steps = config['max_tool_steps']
tool_timeout = config['tool_timeout_s']

FALLBACK_MESSAGE = 'Sorry, I was unable to come up with a response.'
LENGTH_MESSAGE = 'Sorry, my answer got cut off before I could finish it. Try again, or ask for something shorter.'
REFUSAL_MESSAGE = 'Sorry, I can\'t help with that.'
RATE_LIMIT_MESSAGE = 'I\'m being rate limited right now. Please try again in a minute.'
PROVIDER_MESSAGE = 'The model provider is having trouble right now. Please try again shortly.'
CREDITS_MESSAGE = 'I\'m out of credits right now. Please let the bot owner know.'
REQUEST_MESSAGE = 'Something went wrong with that request. Please try again.'

# Public | Methods used in client.py
# message: text to store as a user message before the turn, e.g. an 'API: ...' event written by the caller. It is
# stored here, after the lock is taken, so it cannot land in the middle of another turn of the same chat. Use it
# for turns that are not started by a user message (reminders, see issue #32).
# raise_errors: let openai.APIError propagate instead of returning an error message, for callers with a fallback.
async def get_response(instance, message=None, raise_errors=False):
	# One loop per chat at a time, otherwise two quick messages interleave their context.
	async with instance.lock:
		if message:
			instance.add_user_message(message)
		await instance.load_memory()
		try:
			return await _run_turn(instance)
		except openai.APIError as e:
			if raise_errors:
				raise
			# Not stored in the context: the user's message stays, so they can simply retry.
			return _error_message(e)

async def _run_turn(instance):
	instance.drop_reasoning()
	# Chosen once per turn so every step of a tool loop uses the same model.
	model = Router.route(instance) if instance.auto_route else instance.model
	for _ in range(max_tool_steps):
		response = await ChatCompletion.create(instance.get_context(), model, tools, instance.unique_id)
		if response.finish_reason != 'tool_calls' or not response.message.tool_calls:
			return _finish(instance, response)

		logger.info(f'Tools called: {len(response.message.tool_calls)}')
		calls = await asyncio.gather(*[_process_tool_call(instance, tool_call) for tool_call in response.message.tool_calls])
		reasoning_details = (getattr(response.message, 'model_extra', None) or {}).get('reasoning_details')
		instance.add_tool_round(calls, reasoning_details)

	logger.warning(f'Tool step limit ({max_tool_steps}) reached, forcing a final answer.')
	return _finish(instance, await ChatCompletion.create(instance.get_context(), model, tools, instance.unique_id, tool_choice='none'))

# Maps an API failure (after the SDK's own retries) to a short message for the chat.
def _error_message(error):
	status = getattr(error, 'status_code', None)
	if status == 402:
		logger.critical('OpenRouter reports insufficient credits. Top up the account.')
		return CREDITS_MESSAGE
	logger.error(f'LLM call failed: {type(error).__name__}: {error}')
	if status == 429:
		return RATE_LIMIT_MESSAGE
	if isinstance(error, (openai.APITimeoutError, openai.APIConnectionError)) or (status or 0) >= 500:
		return PROVIDER_MESSAGE
	return REQUEST_MESSAGE

# Returns (tool_call_id, tool_name, tool_arguments, tool_response). Never raises; failures go back to the model.
async def _process_tool_call(instance, tool_call):
	tool_call_id	= tool_call.id # String
	tool_name		= tool_call.function.name # String
	try:
		tool_arguments = json.loads(tool_call.function.arguments) # Dict
	except json.JSONDecodeError as e:
		return tool_call_id, tool_name, {}, {'ok': False, 'error': f'Invalid JSON in tool arguments: {e}'}

	try:
		logger.info(f'{tool_name} tool called.')
		ctx = ToolContext(instance=instance, client=Client)
		tool_response = await asyncio.wait_for(registry.call(tool_name, tool_arguments, ctx), tool_timeout)
	except asyncio.TimeoutError:
		logger.error(f'{tool_name} tool timed out after {tool_timeout}s.')
		tool_response = {'ok': False, 'error': f'The tool timed out after {tool_timeout} seconds.'}
	except Exception as e:
		logger.exception(f'{tool_name} tool failed.')
		tool_response = {'ok': False, 'error': f'{type(e).__name__}: {e}'}
	return tool_call_id, tool_name, tool_arguments, tool_response

# Handles every finish reason other than a tool call: stop, length, content_filter, error and anything unknown.
def _finish(instance, response):
	content = response.message.content
	reason = response.finish_reason
	if content and reason in ('stop', 'length'):
		instance.add_assistant_message(content)
		return content
	logger.warning(f'No usable answer, finish_reason={reason}.')
	if reason == 'length':
		# Reasoning shares max_tokens with the reply, so the budget can run out before any text appears.
		return LENGTH_MESSAGE
	if reason == 'content_filter' or getattr(response.message, 'refusal', None):
		return REFUSAL_MESSAGE
	return FALLBACK_MESSAGE

# Long edge in pixels. Enough to read small print on posters and whiteboards (see docs/agent-loop.md, issue #42).
IMAGE_MAX_EDGE = 1568

def encode_image(data):
	with Image.open(io.BytesIO(data)) as img:
		# A JPEG that already fits is sent as it is, so it is not compressed a second time.
		if img.format == 'JPEG' and max(img.size) <= IMAGE_MAX_EDGE:
			sent_data = data
		else:
			resized = img.convert('RGB')
			resized.thumbnail((IMAGE_MAX_EDGE, IMAGE_MAX_EDGE), Image.Resampling.LANCZOS)

			buffer = io.BytesIO()
			resized.save(buffer, format='JPEG', quality=95)
			sent_data = buffer.getvalue()

	encoded_image = base64.b64encode(sent_data).decode('utf-8')
	return f'data:image/jpeg;base64,{encoded_image}'

logger.info('Assistant object initialized.')