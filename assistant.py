# from logging_config import logger

import openai
import json
import io
import base64
import os
import asyncio
from enum import Enum
import xml.etree.ElementTree as ET
from PIL import Image

# TODO: 
# Brain rot mode : RotBot as it is
# Brain nourishment : gpt-4o full, low temperature, even more limited context
class Personality(Enum):
	BASE = 0
	ROTBOT = 1
	LOCKIN = 2
	WALLSTREET = 3

# Struct to manage each Assistant in a chat
class AssistantInstance:
	def __init__(self, chat_id):
		self.id 				= chat_id
		self.model 				= None
		self.system_prompt 		= None
		self.always_on_prompt 	= None
		self.custom_instruction = None
		self.memory 			= []
		self.context_window		= []
		self.context_length		= 16
		self.personality		= Personality.ROTBOT
		self._load_config()
		self.set_personality(self.personality)

	def _load_config(self):
		tree = ET.parse(f'./config/personality/base.xml')
		root = tree.getroot()
		self.model = root.find('model').text
		self.system_prompt = root.find('system_prompt').text
		self.always_on_prompt = root.find('always_on_prompt').text
		print(self.always_on_prompt)

	def set_personality(self, personality):
		self.personality = personality
		files = {
			Personality.BASE : 'base.xml',
			Personality.ROTBOT : 'rotbot.xml',
			Personality.LOCKIN : 'lockin.xml',
			Personality.WALLSTREET : 'rotbot.xml',
		}

		# Personality to XML
		file = files[personality]
		tree = ET.parse(f'config/personality/{file}')
		root = tree.getroot()

		model = root.find('model')
		custom_instruction = root.find('custom_instruction')

		self.model = model.text
		self.custom_instruction = custom_instruction.text

	def get_system_prompt(self):
		processed_system_prompt = self.system_prompt
		# processed_system_prompt = processed_system_prompt.replace('MEMORY', self.get_memory(chat_id))
		return [
			{
				'role': 'system', 
				'content': processed_system_prompt+self.custom_instruction
			},
		]

	def get_context(self):
		return self.get_system_prompt()+self.context_window

	def get_always_on_prompt(self):
		return [
			{
				'role': 'system', 
				'content': self.always_on_prompt
			},
		]

	def get_always_on_context(self):
		query = [
			{
				'role' : 'user',
				'content' : 'Based on the context given, should RotBot, the assistant, respond?'
			},
		]

		return self.get_always_on_prompt()+self.context_window+query

class Assistant:
	def __init__(self, api_key, model='gpt-4o-mini'):
		self.client = openai.AsyncOpenAI(api_key=api_key)
		self.instances = {}
		self.tools = []
		self._load_config()
		self._init_data()
		print('Assistant object initialized.\n')

	# Public | Methods used in client.py
	async def get_response(self, chat_id):
		# TODO: Create abstraction response class
		instance = self._get_instance(chat_id)

		response = await self.client.chat.completions.create(
			model=instance.model,
			messages=instance.get_context(),
			#stream=True
			tools=self.tools,
			parallel_tool_calls=False
		)
		return response.choices[0]

	# Ideally rewrite these to setget methods on AssistantInstance
	def add_user_message(self, chat_id, text, image_url=None):
		# if not chat_id in self.context_window:
		# 	self.context_window[chat_id] = []
		# instance = self.instances[chat_id]
		instance = self._get_instance(chat_id)

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

		# self.context_window[chat_id].append(user_chat)
		instance.context_window.append(user_chat)

		print('>', instance.context_window[-1]['content'][0]['text'], '\n')

		self._limit_context_window(chat_id)

	def add_assistant_message(self, chat_id, text):
		instance = self._get_instance(chat_id)

		instance.context_window.append(
			{
				'role': 'assistant',
				'content': text,
			}
		)
		print('> RotBot: ', ' '.join(instance.context_window[-1]['content'].splitlines()), '\n')

	def add_system_message(self, chat_id, text):
		instance = self._get_instance(chat_id)

		instance.context_window.append(
			{
				'role': 'system',
				'content': text,
			}
		)
		print('> System: ', ' '.join(instance.context_window[-1]['content'].splitlines()), '\n')

	def add_tool_message(self, chat_id, tool_call, tool_output):
		instance = self._get_instance(chat_id)
		print(f'{tool_call.function.name} tool usage saved in context!', '\n')
		instance.context_window.append(
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
		instance.context_window.append(
			{
				'role' : 'tool',
				'tool_call_id': tool_call.id,
				'content' : json.dumps(tool_output),
			}
		)# Change params here to accomodate multiple backends

	def set_instance_personality(self, chat_id, personality):
		instance = self._get_instance(chat_id)	
		instance.set_personality(personality)

	async def is_user_addressing(self, chat_id):
		instance = self._get_instance(chat_id)

		print(instance.model, instance.get_always_on_context())

		completion = await self.client.chat.completions.create(
			model=instance.model,
			messages=instance.get_always_on_context() # TODO add memory here
		)

		answer = completion.choices[0].message.content.strip().lower()

		print(f'[VERDICT] {answer}', '\n')

		return answer == 'yes'

	async def encode_image(self, data):
		encoded_image = base64.b64encode(data).decode('utf-8')
		return f'data:image/jpeg;base64,{encoded_image}'

	async def summarize(self, content, focus=None):
		# TODO: Create abstraction response class
		query = []
		if focus:
			query = [
				{
					'role' : 'user',
					'content' : f'Summarize the following text, with a special focus on the topic: \'{focus}\'. Include key details about {focus} prominently in your summary, while briefly mentioning other relevant details.'
				},
			]
		else:
			query = [
				{
					'role' : 'user',
					'content' : f'Summarize the following text. {content}'
				},
			]

		completion = await self.client.chat.completions.create(
			model='gpt-4o-mini',
			messages=query
		)
		return completion.choices[0].message.content

	async def generate_image(self, prompt):
		response = await self.client.images.generate(
			model="dall-e-3",
			prompt=prompt,
			size="1024x1024",
			quality="standard",
			n=1,
		)
		image_url = response.data[0].url
		return image_url

	# Assistant tools
	def add_to_memory(self, content, chat_id):
		if not chat_id in self.memory:
			self.memory[chat_id] = []
		self.memory[chat_id].append(content)
		self._dump()
		return {
			'info' : content,
			'state' : 'Information successfully saved.'
		}

	# Private | Methods only for assistant.py
	def _get_instance(self, chat_id):
		if not chat_id in self.instances:
			self.instances[chat_id] = AssistantInstance(chat_id)
		instance = self.instances[chat_id]
		return instance

	def _limit_context_window(self, chat_id):
		instance = self.instances[chat_id]

		instance.context_window = instance.context_window[-instance.context_length:]
		last_chat = instance.context_window[0]
		if last_chat['role'] == 'tool':
			# Remove both tool_calls and tool 
			instance.context_window = instance.context_window[1:]
	
	def _load_config(self):
		# Should probably move back the always on thing here
		with (
			# open('config/always_on_prompt.txt') as always_on_prompt_file, 
			open('config/tools.json') as tools_file, 
		):
			# self.always_on_prompt = [
			# 	{
			# 		'role': 'system', 
			# 		'content': always_on_prompt_file.read()
			# 	},
			# ]
			self.tools = json.loads(tools_file.read())

	def _init_data(self):
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

	def _dump(self):
		with open('data/memory.json', 'w') as file:
			file.write(json.dumps(self.memory, indent=4))
