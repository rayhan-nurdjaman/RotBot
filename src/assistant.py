import logging

import io
import json
import base64
import asyncio
from PIL import Image
from ruamel.yaml import YAML

import tool_manager as ToolManager
import chat_completion as ChatCompletion

logger = logging.getLogger(__name__)

with open('config/tools.json') as tools_file:
	tools = json.loads(tools_file.read())

with open('config/config.yaml') as config_file:
	max_tool_steps = YAML(typ='safe', pure=True).load(config_file)['max_tool_steps']

FALLBACK_MESSAGE = 'Sorry, I was unable to come up with a response.'

# Public | Methods used in client.py
async def get_response(instance):
	# One loop per chat at a time, otherwise two quick messages interleave their context.
	async with instance.lock:
		for _ in range(max_tool_steps):
			response = await ChatCompletion.create(instance.get_context(), instance.model, tools)
			if response.finish_reason != 'tool_calls' or not response.message.tool_calls:
				return _finish(instance, response)

			logger.info(f'Tools called: {len(response.message.tool_calls)}')
			calls = await asyncio.gather(*[_process_tool_call(instance, tool_call) for tool_call in response.message.tool_calls])
			instance.add_tool_round(calls)

		logger.warning(f'Tool step limit ({max_tool_steps}) reached, forcing a final answer.')
		return _finish(instance, await ChatCompletion.create(instance.get_context(), instance.model))

# Returns (tool_call_id, tool_name, tool_arguments, tool_response). Never raises; failures go back to the model.
async def _process_tool_call(instance, tool_call):
	tool_call_id	= tool_call.id # String
	tool_name		= tool_call.function.name # String
	try:
		tool_arguments = json.loads(tool_call.function.arguments) # Dict
	except json.JSONDecodeError as e:
		return tool_call_id, tool_name, {}, {'error': f'Invalid JSON in tool arguments: {e}'}

	try:
		tool_response = await ToolManager.get_tool_response(instance, tool_name, tool_arguments)
	except Exception as e:
		logger.exception(f'{tool_name} tool failed.')
		tool_response = {'error': f'{type(e).__name__}: {e}'}
	return tool_call_id, tool_name, tool_arguments, tool_response

# Handles every finish reason other than a tool call: stop, length, content_filter, error and anything unknown.
def _finish(instance, response):
	content = response.message.content
	if response.finish_reason not in ('stop', 'length') or not content:
		logger.warning(f'No usable answer, finish_reason={response.finish_reason}.')
		return FALLBACK_MESSAGE
	instance.add_assistant_message(content)
	return content

def encode_image(data):
	image_max_res = 512
	with Image.open(io.BytesIO(data)) as img:
		new_size = (image_max_res, image_max_res)

		img.thumbnail(new_size, Image.Resampling.LANCZOS)

		buffer = io.BytesIO()
		img.save(buffer, format='JPEG')
		buffer.seek(0)
		
		resized_data = buffer.read()

	encoded_image = base64.b64encode(resized_data).decode('utf-8')
	return f'data:image/jpeg;base64,{encoded_image}'

logger.info('Assistant object initialized.')