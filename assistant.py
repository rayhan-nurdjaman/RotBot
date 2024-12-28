import openai
import json
import io
import base64
import os
import asyncio
from enum import Enum
from PIL import Image
from ruamel.yaml import YAML

class Personality(Enum):
	ROTBOT = 0
	CODER = 1
	CAVEMAN = 2
	BRITISH = 3

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
		self.temperature		= 1.0
		self.personality		= Personality.ROTBOT
		self._load_config()
		self.set_personality(self.personality)

	def _load_config(self):
		with open('config/base.yaml') as stream:
			yaml = YAML(typ='safe', pure=True)
			root = yaml.load(stream)
			self.model = root['model']
			self.system_prompt = root['system_prompt']
			self.temperature = root['temp']
			self.context_length = root['context_length']

	def set_personality(self, personality):
		self.personality = personality
		files = {
			Personality.ROTBOT : 'personality/rotbot.yaml',
			Personality.CODER : 'personality/coder.yaml',
			Personality.CAVEMAN : 'personality/caveman.yaml',
			Personality.BRITISH : 'personality/british.yaml',
		}
		file = files[personality]
		with open(f'config/{file}') as stream:
			yaml = YAML(typ='safe', pure=True)
			root = yaml.load(stream)
			self.model = root['model']
			self.custom_instruction = root['custom_instruction']
			self.temperature = root['temp']
			self.context_length = root['context_length']

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

	# Unsure whether to put this here or in the assistant class, as the name will always be RotBot
	# Most likely need to revert back to there
	# def get_always_on_prompt(self):
	# 	return [
	# 		{
	# 			'role': 'system', 
	# 			'content': self.always_on_prompt
	# 		},
	# 	]

	# TODO: Improve reliability, maybe incorporate CoT?
	def get_always_on_context(self, prompt):
		query = [
			{
				'role' : 'user',
				'content' : 'Based on the context given, should RotBot, the assistant, respond?'
			},
		]
		always_on_prompt = [
			{
				'role' : 'system',
				'content' : prompt
			},
		]

		return always_on_prompt+self.context_window+query

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

		# TODO: Somehow implement streaming without integrating too hard with client.py
		# TODO: Error handling here
		response = await self.client.chat.completions.create(
			model=instance.model,
			messages=instance.get_context(),
			#stream=True
			tools=self.tools,
			parallel_tool_calls=False,
			temperature=instance.temperature
		)
		return response.choices[0]

	def add_user_message(self, chat_id, text, image_url=None):
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

		# TODO: Add a set method for the context window?
		instance.context_window.append(user_chat)

		# TODO: Do logging
		print('>', instance.context_window[-1]['content'][0]['text'], '\n')

		# TODO: move this to the set method on the instance
		self._limit_context_window(chat_id)

	def add_assistant_message(self, chat_id, text):
		instance = self._get_instance(chat_id)

		# TODO: Add a set method for the context window?
		instance.context_window.append(
			{
				'role': 'assistant',
				'content': text,
			}
		)
		# TODO: Do logging
		print('> RotBot: ', ' '.join(instance.context_window[-1]['content'].splitlines()), '\n')

	def add_system_message(self, chat_id, text):
		instance = self._get_instance(chat_id)

		# TODO: Add a set method for the context window?
		instance.context_window.append(
			{
				'role': 'system',
				'content': text,
			}
		)
		# TODO: Do logging
		print('> System: ', ' '.join(instance.context_window[-1]['content'].splitlines()), '\n')

	def add_tool_message(self, chat_id, tool_call, tool_output):
		instance = self._get_instance(chat_id)
		# TODO: Do logging
		print(f'{tool_call.function.name} tool usage saved in context!', '\n')
		# TODO: Add a set method for the context window?
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
		# TODO: Add a set method for the context window?
		instance.context_window.append(
			{
				'role' : 'tool',
				'tool_call_id': tool_call.id,
				'content' : json.dumps(tool_output),
			}
		)# Change params here to accomodate multiple backends

	# TODO: Probably want to add user specified personalities? So most likely change from enums to strings
	def set_instance_personality(self, chat_id, personality):
		instance = self._get_instance(chat_id)
		instance.set_personality(personality)

	# TODO: Improve reliability
	async def is_user_addressing(self, chat_id):
		instance = self._get_instance(chat_id)

		completion = await self.client.chat.completions.create(
			model=self.always_on_model,
			messages=instance.get_always_on_context(self.always_on_prompt) # TODO add memory here
		)

		answer = completion.choices[0].message.content.strip().lower()

		print(f'[VERDICT] {answer}', '\n')

		return answer == 'yes'

	# TODO: Resize images here to reduce network use
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
		with (
			open('config/tools.json') as tools_file, 
			open('config/always_on.yaml') as stream
		):
			yaml = YAML(typ='safe', pure=True)
			root = yaml.load(stream)
			self.always_on_prompt = root['always_on_prompt']
			self.always_on_model = root['model']
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
