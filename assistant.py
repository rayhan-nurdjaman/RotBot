import openai
import json
import io
import base64
import os
import asyncio
from enum import Enum
from PIL import Image
from ruamel.yaml import YAML

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
		self.personality		= 'rotbot'
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

	def set_personality(self, key):
		self.personality = key
		files = {
			'rotbot' : 'personality/rotbot.yaml',
			'coder' : 'personality/coder.yaml',
			'caveman' : 'personality/caveman.yaml',
			'british' : 'personality/british.yaml',
			'alien' : 'personality/alien.yaml',
			'abg' : 'personality/abg.yaml',
			'classic' : 'personality/classic.yaml',
		}
		if key in files:
			file = files[key]
			with open(f'config/{file}') as stream:
				yaml = YAML(typ='safe', pure=True)
				root = yaml.load(stream)
				self.model = root['model']
				self.custom_instruction = root['custom_instruction']
				self.temperature = root['temp']
				self.context_length = root['context_length']
			return True
		else:
			return False

	def get_system_prompt(self):
		processed_system_prompt = self.system_prompt
		# processed_system_prompt = processed_system_prompt.replace('MEMORY', self.get_memory(chat_id))
		return [
			{
				'role': 'system', 
				'content': processed_system_prompt+self.custom_instruction
			},
		]

	def add_to_context(self, message):
		self.context_window.append(message)
		self.context_window = self.context_window[-self.context_length:]
		last_chat = self.context_window[0]
		if last_chat['role'] == 'tool':
			# Remove both tool_calls and tool 
			self.context_window = self.context_window[1:]

	def get_context(self):
		return self.get_system_prompt()+self.context_window

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

		message = {
			'role': 'user',
			'name' : 'MESSAGING_API',
			'content': []
		}

		if text:
			message['content'].append(
				{
					'type': 'text',
					'text': text
				}
			)
		if image_url and False:
			message['content'].append(
				{
					'type': 'image_url',
					'image_url': {
						'url': image_url,
						"detail": "low"
					}
				}
			)

		instance.add_to_context(message)

		# TODO: Do logging
		print('>', instance.context_window[-1]['content'][0]['text'], '\n')

	def add_assistant_message(self, chat_id, text):
		instance = self._get_instance(chat_id)

		message = {
			'role': 'assistant',
			'content': text,
		}

		instance.add_to_context(message)
		# TODO: Do logging
		print('> RotBot: ', ' '.join(instance.context_window[-1]['content'].splitlines()), '\n')

	def add_system_message(self, chat_id, text):
		instance = self._get_instance(chat_id)

		message = {
			'role': 'system',
			'content': text,
		}

		instance.add_to_context(message)
		# TODO: Do logging
		print('> System: ', ' '.join(instance.context_window[-1]['content'].splitlines()), '\n')

	def add_tool_message(self, chat_id, tool_call, tool_output):
		instance = self._get_instance(chat_id)

		tool_call_message = {
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

		instance.add_to_context(tool_call_message)

		tool_call_response = {
			'role' : 'tool',
			'tool_call_id': tool_call.id,
			'content' : json.dumps(tool_output),
		}

		instance.add_to_context(tool_call_response)
		# TODO: Do logging
		print(f'{tool_call.function.name} tool usage saved in context!', '\n')

	def set_instance_personality(self, chat_id, key):
		instance = self._get_instance(chat_id)
		return instance.set_personality(key)

	def encode_image(self, data):
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

	async def summarize(self, content, focus=None):
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
		try:
			response = await self.client.images.generate(
				model="dall-e-3",
				prompt=prompt,
				size="1024x1024",
				quality="standard",
				n=1,
			)
			image_url = response.data[0].url
			return image_url
		except:
			return None

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

	# Convert to db use?
	def _dump(self):
		with open('data/memory.json', 'w') as file:
			file.write(json.dumps(self.memory, indent=4))
