import logging

import os
import json
import asyncio
from ruamel.yaml import YAML

from config import config
from models import DEFAULT_MODEL

logger = logging.getLogger(__name__)

personality_index = {}
personality_dir = 'config/personality/'
memory_file = 'data/memory.json'

# Returns a copy of the message with a cache breakpoint on its last content part, or the message itself if
# it has no content to attach one to. The stored context is never modified, so breakpoints never pile up.
def _with_cache_breakpoint(message):
	content = message.get('content')
	if isinstance(content, str) and content:
		parts = [{'type': 'text', 'text': content}]
	elif isinstance(content, list) and content:
		parts = [dict(part) for part in content]
	else:
		return message
	parts[-1]['cache_control'] = {'type': 'ephemeral'}
	return {**message, 'content': parts}

def _read_memory_file():
	try:
		with open(memory_file) as stream:
			return json.load(stream)
	except FileNotFoundError:
		return {}

with open(f'{personality_dir}index.yaml') as stream:
	yaml = YAML(typ='safe', pure=True)
	personality_index = yaml.load(stream)

class Reminder:
	def __init__(self):
		self.unix_time
		self.description

#TODO: use tiktoken to ensure context length.
class Instance:
	def __init__(self, unique_id):
		self.unique_id 			= unique_id
		self.always_on			= True
		# Personality
		self.personality		= 'rotbot'
		self.modalities			= []
		self.model 				= DEFAULT_MODEL # models.ModelSpec, set with /model
		self.auto_route			= False # when True, router.route() picks the model each turn and self.model is ignored
		self.system_prompt 		= None
		self.custom_instruction = None
		self.enable_vision		= False
		self.context_length		= 16

		self.memory 			= _read_memory_file().get(str(unique_id), [])
		self.context_window		= []
		self.lock				= asyncio.Lock()

		self.zone_offset		= 8

		self.set_personality('base')
		self.set_personality(self.personality)

	def set_personality(self, key):
		self.personality = key
		file = personality_dir + personality_index[key]
		with open(file) as stream:
			yaml = YAML(typ='safe', pure=True)
			root = yaml.load(stream)

			self.modalities = root['modalities'] if 'modalities' in root else self.modalities

			self.system_prompt = root['system_prompt'] if 'system_prompt' in root else self.system_prompt
			self.context_length = root['context_length'] if 'context_length' in root else self.context_length
			self.custom_instruction = root['custom_instruction'] if 'custom_instruction' in root else self.custom_instruction

	# Ordered so the front of the request stays identical between requests, which is what prompt caching needs:
	# static system prompt, append-only history with one cache breakpoint on its last message, then volatile data.
	def get_context(self):
		window = list(self.context_window)
		if window:
			window[-1] = _with_cache_breakpoint(window[-1])
		return self.get_system_prompt()+window+self.get_volatile_messages()

	# Adding to context; convenience functions
	def add_to_context(self, message):
		self.context_window.append(message)
		self._trim_context()

		# encoding = tiktoken.encoding_for_model(self.model)
		# tokens = encoding.encode("Hello, world!")  # Returns a list of token integers
		# num_tokens = len(tokens)
		# while num_tokens > 128000:
		# 	tokens = encoding.encode("Hello, world!")  # Returns a list of token integers
		# 	num_tokens = len(tokens)


	def add_user_message(self, text, image_url=None):
		message = {
			'role': 'user',
			'content': []
		}

		if text:
			message['content'].append(
				{
					'type': 'text',
					'text': text
				}
			)
		if image_url:
			message['content'].append(
				{
					'type': 'image_url',
					'image_url': {
						'url': image_url,
						"detail": "low"
					}
				}
			)

		self.add_to_context(message)
		logger.info(f'{text or "[image]"}\n')

	def add_assistant_message(self, text):
		message = {
			'role': 'assistant',
			'content': [
				{
					'type': 'text',
					'text': text	
				}
			]
		}

		self.add_to_context(message)
		logger.info(f'RotBot: {text}\n')

	def add_system_message(self, text):
		message = {
			'role': 'system',
			'content': [
				{
					'type': 'text',
					'text': text	
				}
			]
		}

		self.add_to_context(message)
		logger.info(f'RotBot: {text}\n')

	# calls: list of (tool_call_id, tool_name, tool_arguments, tool_response), in the order the model made them.
	# Stored as one assistant message holding every tool call, then one tool message per call.
	# reasoning_details: the model's reasoning blocks from this response. Providers need them passed back
	# unchanged with the tool results (OpenRouter: consecutive blocks must match what was returned).
	def add_tool_round(self, calls, reasoning_details=None):
		assistant_message = {
			'role': 'assistant',
			'tool_calls': [
				{
					'id': tool_call_id,
					'type': 'function',
					'function': {
						'name': tool_name,
						'arguments': json.dumps(tool_arguments),
					}
				}
				for tool_call_id, tool_name, tool_arguments, _ in calls
			]
		}
		if reasoning_details:
			assistant_message['reasoning_details'] = reasoning_details
		self.context_window.append(assistant_message)
		for tool_call_id, _, _, tool_response in calls:
			self.context_window.append({
				'role' : 'tool',
				'tool_call_id': tool_call_id,
				'content' : json.dumps(tool_response),
			})
		self._trim_context()

	# Reasoning blocks are bound to the model that produced them, and the model can change between turns,
	# so they are only kept for the tool loop of the turn that made them.
	def drop_reasoning(self):
		for message in self.context_window:
			message.pop('reasoning_details', None)

	def add_memory(self, content):
		self.memory.append(content)
		all_memory = _read_memory_file()
		all_memory[str(self.unique_id)] = self.memory
		os.makedirs(os.path.dirname(memory_file), exist_ok=True)
		with open(memory_file, 'w') as stream:
			json.dump(all_memory, stream, indent=4)
		return {
			'state' : 'Information successfully saved.',
			'memory' : content,
		}

	# def add_to_memory(content):
	# 	self.memory.append(content)
	# 	with open(f'data/memory/{self.unique_id}.json', 'w') as file:
	# 		file.write(json.dumps(self.memory, indent=4))

	# Private
	def _trim_context(self):
		# Trimmed in chunks, not one message at a time: a sliding window changes the front of the history on
		# every message, so the prompt cache would never hit. The window grows by up to a chunk, then is cut
		# back to context_length.
		chunk = max(1, int(self.context_length * config['context_trim_fraction']))
		if len(self.context_window) > self.context_length + chunk:
			self.context_window = self.context_window[-self.context_length:]
		# Trimming only cuts from the front, so the only way to orphan a tool message is for the
		# window to start inside a group of tool results. Drop those; their tool_calls message is gone.
		while self.context_window and self.context_window[0]['role'] == 'tool':
			self.context_window.pop(0)

	# Data that changes between requests goes last, after the cache breakpoint, so it never invalidates the prefix.
	# Never stored in the context.
	def get_volatile_messages(self):
		if not self.memory:
			return []
		entries = '\n'.join(f'- {entry}' for entry in self.memory)
		return [{
			'role': 'user',
			'content': [{
				'type': 'text',
				'text': f'API: Current memory (reference only, not a message from a user; keep responding to the conversation above):\n{entries}'
			}]
		}]

	def get_system_prompt(self):
		processed_system_prompt = self.system_prompt
		return [
			{
				'role': 'system', 
				'content': [
					{
						'type': 'text',
						'text': processed_system_prompt+self.custom_instruction	
					}
				]
			}
		]

	# def _set_personality(self, path):
