# from logging_config import logger

import openai
import json
import io
import base64
import os
from enum import Enum

# TODO: 
# Brain rot mode : RotBot as it is
# Brain nourishment : gpt-4o full, low temperature, even more limited context

class Assistant:
	def __init__(self, api_key, model='gpt-4o-mini'):
		self.client = openai.OpenAI(api_key=api_key)
		self.model = model
		self.context_window = {}
		self.context_window_max = 16
		self.memory = {}
		self.tools = []
		with (
			open('config/system_prompt.txt') as system_prompt_file, 
			open('config/always_on_prompt.txt') as always_on_prompt_file, 
			open('config/tools.json') as tools_file, 
			open('config/brain_rot.json') as brain_rot_file
		):
			self.raw_system_prompt = system_prompt_file.read()
			self.always_on_prompt = [
				{
					'role': 'system', 
					'content': always_on_prompt_file.read()
				},
			]
			self.tools = json.loads(tools_file.read())
			self.brain_rot_terms = brain_rot_file.read()

		if not os.path.exists('data'):
			os.makedirs('data')
		try:
			with open('data/memory.json', 'r') as memory_file:
				raw_memory = memory_file.read()
				self.memory = json.loads(raw_memory) if raw_memory else {}
		except FileNotFoundError:
			with open('data/memory.json', 'w') as memory_file:
				memory_file.write(json.dumps({}))
				self.memory = {}
		print('Assistant object initialized.\n')

	async def get_response(self, chat_id):
		# TODO: Create abstraction response class
		response = self.client.chat.completions.create(
			model=self.model,
			messages=self.get_system_prompt(chat_id)+self.context_window[chat_id],
			tools=self.tools
		)
		return response.choices[0]

	def add_user_message(self, chat_id, text, image_url=None):
		if not chat_id in self.context_window:
			self.context_window[chat_id] = []

		user_chat = {
			'role': 'user',
			'name' : 'MESSAGING_API',
			'content': []
		}

		if text:
			user_chat['content'].append(
				{
					'type': 'text',
					'text': text
				}
			)
		if image_url:
			user_chat['content'].append(
				{
					'type': 'image_url',
					'image_url': {
						'url': image_url,
						"detail": "low"
					}
				}
			)

		self.context_window[chat_id].append(user_chat)

		print('>', self.context_window[chat_id][-1]['content'][0]['text'], '\n')

		self._limit_context_window(chat_id)

	def add_assistant_message(self, chat_id, response):
		self.context_window[chat_id].append(
			{
				'role': 'assistant',
				'content': response.message.content,
			}
		)
		print('> RotBot: ', ' '.join(self.context_window[chat_id][-1]['content'].splitlines()), '\n')

	def add_tool_message(self, chat_id, tool_call, tool_output):
		print(f'{tool_call.function.name} tool usage saved in context!', '\n')
		self.context_window[chat_id].append(
			{
				'role': 'assistant',
				'tool_calls': [
					{
					'id': tool_call.id,
					'type': tool_call.type,
					'function': {
							'arguments': tool_call.function.arguments,
							'name': tool_call.function.name
						}
					}
				]
			}
		)
		self.context_window[chat_id].append(
			{
				'role' : 'tool',
				'tool_call_id': tool_call.id,
				'content' : json.dumps(tool_output),
			}
		)

	def is_user_addressing(self, chat_id):
		query = [
			{
				'role' : 'user',
				'content' : 'Based on the context given, should the assistant respond?'
			},
		]

		# TODO: Add memory to system prompt here
		completion = self.client.chat.completions.create(
			model=self.model,
			messages=self.always_on_prompt+self.context_window[chat_id]+query # TODO add memory here
		)

		answer = completion.choices[0].message.content.strip().lower()

		print(f'[VERDICT] {answer}', '\n')

		return answer == 'yes'

	def dump(self):
		with open('data/memory.json', 'w') as file:
			file.write(json.dumps(self.memory, indent=4))

	# Assistant tools
	def add_to_memory(self, content, chat_id):
		if not chat_id in self.memory:
			self.memory[chat_id] = {}
		self.memory[chat_id].append(content)
		self.dump()
		return {
			'info' : content,
			'state' : 'Information successfully saved.'
		}

	# Helper functions
	def _limit_context_window(self, chat_id):
		self.context_window[chat_id] = self.context_window[chat_id][-self.context_window_max:]
		last_chat = self.context_window[chat_id][0]
		if last_chat['role'] == 'tool':
			# Remove both tool_calls and tool 
			self.context_window[chat_id] = self.context_window[chat_id][1:]

	async def encode_image(self, data):
		encoded_image = base64.b64encode(data).decode('utf-8')
		return f'data:image/jpeg;base64,{encoded_image}'

	def get_system_prompt(self, chat_id):
		processed_system_prompt = self.raw_system_prompt
		processed_system_prompt = processed_system_prompt.replace('BRAIN_ROT_TERMS', self.brain_rot_terms)
		processed_system_prompt = processed_system_prompt.replace('MEMORY', json.dumps(self.memory[chat_id]))
		return [
			{
				'role': 'system', 
				'content': processed_system_prompt
			},
		]

	def get_always_on_prompt(self, chat_id):
		pass
