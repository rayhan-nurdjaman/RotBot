import logging

import io
import os
import json
import base64
import asyncio
from PIL import Image

import tool_manager as ToolManager
import chat_completion as ChatCompletion

logger = logging.getLogger(__name__)

class Response:
	def __init__(self):	
		self.iterable
		self.message
		self.tool_call # name, params

with open('config/tools.json') as tools_file:
	tools = json.loads(tools_file.read())

# Public | Methods used in client.py
async def get_response(instance):
	response = await ChatCompletion.create(instance.get_context(), instance.model, tools)

	while response.finish_reason != 'stop':
		logger.info(f'Tools called: {len(response.message.tool_calls)}')

		async def process_tool_call(tool_call):
			tool_call_id	= tool_call.id # String
			tool_name		= tool_call.function.name # String
			tool_arguments	= json.loads(tool_call.function.arguments) # Dict

			tool_response = await ToolManager.get_tool_response(instance, tool_name, tool_arguments)
			# Not thread safe
			instance.add_tool_call(tool_call_id, tool_name, tool_arguments)
			instance.add_tool_response(tool_call_id, tool_response)

		tasks = [process_tool_call(tool_call) for tool_call in response.message.tool_calls]
		await asyncio.gather(*tasks)

		response = await ChatCompletion.create(instance.get_context(), instance.model, tools)

	instance.add_assistant_message(response.message.content) # Finish reason == 'stop'

	return response.message.content

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

# Assistant tools
def add_to_memory(content, chat_id):
	if not chat_id in self.memory:
		self.memory[chat_id] = []
	self.memory[chat_id].append(content)
	self._dump()
	return {
		'state' : 'Information successfully saved.',
		'memory' : content,
	}

# Overhaul this
def _init_data():
	if not os.path.exists('data'):
		os.makedirs('data')
	try:
		with open('data/memory.json', 'r') as memory_file:
			raw_memory = memory_file.read()
			memory = json.loads(raw_memory) if raw_memory else {}
	except FileNotFoundError:
		with open('data/memory.json', 'w') as memory_file:
			memory_file.write(json.dumps({}))
			memory = {}

def _dump():
	with open('data/memory.json', 'w') as file:
		file.write(json.dumps(self.memory, indent=4))

_init_data()
logger.info('Assistant object initialized.')