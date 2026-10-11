import logging

import json
import asyncio
from ruamel.yaml import YAML

from config import config
import clock as Clock
import memories as Memories
from models import DEFAULT_MODEL
from tools.native import registry # Importing native registers the tools, which the system prompt lists.

logger = logging.getLogger(__name__)

personality_index = {}
personality_dir = 'config/personality/'

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

		self.memory 			= [] # [{'id', 'text'}], this chat's memories as of the last load_memory()
		self.memory_hidden		= 0 # memories left out of self.memory by the caps in memories.py
		self.context_window		= []
		self.lock				= asyncio.Lock()

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
	# static system prompt, memory (changes only when something is saved), then the append-only history with one
	# cache breakpoint on its last message. Nothing may come after the history: with server tools in the request,
	# anything after the breakpoint disables the prompt cache (see docs/caching-and-server-tools.md and issue #41).
	def get_context(self):
		window = list(self.context_window)
		if window:
			window[-1] = _with_cache_breakpoint(window[-1])
		return self.get_system_prompt()+self.get_memory_messages()+window

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
			# Stamped once, when stored, so the stored text never changes. The model has no clock otherwise.
			text = f'[{Clock.stamp()}] {text}'
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

	# Reads this chat's memories from the database into self.memory. Called once at the start of each turn, not
	# per LLM call, so the prompt prefix stays the same through a tool loop: a memory saved during a turn shows up
	# from the next turn on (one cache write). If the database fails the turn goes on with the previous list.
	async def load_memory(self):
		try:
			self.memory, self.memory_hidden = await Memories.for_prompt(self.unique_id)
		except Exception:
			logger.exception(f'Could not load the memories of chat {self.unique_id}, using the last known list.')

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

	# Stored memory as a user message right after the system prompt, or nothing if there is none. It only changes
	# when a memory is saved or removed, which costs one cache write each. Never stored in the context.
	def get_memory_messages(self):
		if not self.memory:
			return []
		entries = '\n'.join(f'- [{entry["id"]}] {entry["text"]}' for entry in self.memory)
		if self.memory_hidden:
			entries += f'\n({self.memory_hidden} older memories are not shown here, use recall_memory to search them.)'
		return [{
			'role': 'user',
			'content': [{
				'type': 'text',
				'text': f'API: Memory (reference information, not a message from a user; keep responding to the conversation below). Facts and standing instructions saved for this chat, each with its id; follow the instructions:\n{entries}'
			}]
		}]

	def get_system_prompt(self):
		processed_system_prompt = self.system_prompt.replace('{tools}', registry.prompt_section()).replace('{timezone}', Clock.zone.key)
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
