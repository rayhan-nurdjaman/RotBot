import logging

import io
import json
import base64
import asyncio
import openai
from PIL import Image

import access as Access
import approvals as Approvals
from config import config
from tools.native import registry
from tools.registry import ToolContext
import client as Client
import chat_completion as ChatCompletion
import router as Router
import usage as Usage

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
CAP_MESSAGE = 'This chat has reached its spending limit for today. It resets at midnight.'

# What get_response returns instead of text when the model's step contains a call that needs the user's approval
# (see approvals.py). Nothing of the step has run or been stored in the history. The caller asks the user and then
# calls resume() with the answer. The model, the reasoning blocks and the number of steps left are kept so that the
# turn carries on exactly where it stopped. It lives in memory only.
class Suspended:
	def __init__(self, tool_calls, reasoning_details, model, steps_left):
		self.tool_calls = tool_calls
		self.reasoning_details = reasoning_details
		self.model = model
		self.steps_left = steps_left

	# [(tool name, arguments dict)] in the model's order, for showing to the user.
	@property
	def calls(self):
		return [(tool_call.function.name, _arguments(tool_call)) for tool_call in self.tool_calls]

def _arguments(tool_call):
	try:
		arguments = json.loads(tool_call.function.arguments)
	except json.JSONDecodeError:
		return {}
	return arguments if isinstance(arguments, dict) else {}

# True if this step must wait for the user: one of its calls needs approval, the chat is one where approval applies
# (the op chats, the only ones where such tools run at all) and the user has not approved everything for the day.
def _needs_approval(instance, tool_calls):
	flagged = [tool_call for tool_call in tool_calls if registry.needs_approval(tool_call.function.name)]
	if not flagged or not Access.is_op_chat(instance.unique_id):
		return False
	if Approvals.day_approved(instance.unique_id):
		for tool_call in flagged:
			logger.info(f'Running without asking, the chat approved everything for the day: chat {instance.unique_id}, {tool_call.function.name} {_arguments(tool_call)!r:.300}')
		return False
	return True

# Public | Methods used in client.py
# The caller must hold instance.lock, from before it stores the user message until this returns. One turn per chat
# at a time, and nothing may be appended to the context in between, otherwise the request can end with an assistant
# message, which Claude 5.5 rejects (issue #32).
# message: text to store as a user message before the turn, e.g. an 'API: ...' event written by the caller.
# raise_errors: let openai.APIError and usage.CapReached propagate instead of returning an error message, for callers
# with a fallback.
# Returns the reply text, or a Suspended (see above) when the turn has to wait for an approval.
async def get_response(instance, message=None, raise_errors=False):
	if message:
		instance.add_user_message(message)
	try:
		await Usage.check(instance.unique_id)
	except Usage.CapReached:
		if raise_errors:
			raise
		# Not stored in the context, like an API error: the user's message stays.
		return CAP_MESSAGE
	await instance.load_memory()
	try:
		return await _run_turn(instance)
	except openai.APIError as e:
		if raise_errors:
			raise
		# Not stored in the context: the user's message stays, so they can simply retry.
		return _error_message(e)

# Carries on a turn that returned a Suspended, once the user has answered. The caller must hold instance.lock, like
# for get_response. refusal=None means approved: every call of the step runs. Otherwise none of them runs and each
# gets `refusal` (a sentence for the model) as its result, so a deny, an expiry and a failure to ask all look the
# same to the loop. Returns what get_response returns. Between the suspension and now other turns may have added
# messages to the history; the tool round is appended at the end, after them.
async def resume(instance, suspended, refusal=None, raise_errors=False):
	try:
		await Usage.check(instance.unique_id)
	except Usage.CapReached:
		if raise_errors:
			raise
		return CAP_MESSAGE
	try:
		if refusal is None:
			calls = await asyncio.gather(*[_process_tool_call(instance, tool_call) for tool_call in suspended.tool_calls])
		else:
			calls = [
				(tool_call.id, tool_call.function.name, _arguments(tool_call), {'ok': False, 'error': refusal})
				for tool_call in suspended.tool_calls
			]
		instance.add_tool_round(calls, suspended.reasoning_details)
		return await _loop(instance, suspended.model, suspended.steps_left)
	except openai.APIError as e:
		if raise_errors:
			raise
		return _error_message(e)

async def _run_turn(instance):
	instance.drop_reasoning()
	# Chosen once per turn so every step of a tool loop uses the same model.
	model = await Router.route(instance) if instance.auto_route else instance.model
	return await _loop(instance, model, max_tool_steps)

# `steps` tool rounds at most, then one forced answer without tools.
async def _loop(instance, model, steps):
	for step in range(steps):
		response = await ChatCompletion.create(instance.get_context(), model, tools, instance.unique_id)
		if response.finish_reason != 'tool_calls' or not response.message.tool_calls:
			return _finish(instance, response)

		logger.info(f'Tools called: {len(response.message.tool_calls)}')
		reasoning_details = (getattr(response.message, 'model_extra', None) or {}).get('reasoning_details')
		if _needs_approval(instance, response.message.tool_calls):
			logger.info(f'Waiting for approval in chat {instance.unique_id}.')
			return Suspended(response.message.tool_calls, reasoning_details, model, steps - step - 1)
		calls = await asyncio.gather(*[_process_tool_call(instance, tool_call) for tool_call in response.message.tool_calls])
		instance.add_tool_round(calls, reasoning_details)

	logger.warning('Tool step limit reached, forcing a final answer.')
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
		limit = registry.timeout(tool_name) or tool_timeout
		tool_response = await asyncio.wait_for(registry.call(tool_name, tool_arguments, ctx), limit)
	except asyncio.TimeoutError:
		logger.error(f'{tool_name} tool timed out after {limit}s.')
		tool_response = {'ok': False, 'error': f'The tool timed out after {limit} seconds.'}
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