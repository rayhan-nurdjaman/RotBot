import logging

import json
from ruamel.yaml import YAML

logger = logging.getLogger(__name__)

class Reminder:
	def __init__(self):
		self.unix_time
		self.description

class Instance:
	def __init__(self, unique_id):
		self.unique_id 			= unique_id
		self.always_on			= True
		# Personality
		self.personality		= 'classic'
		self.modalities			= ['text', 'image']
		self.model 				= None
		self.system_prompt 		= None
		self.custom_instruction = None
		self.enable_vision		= False
		self.context_length		= 16
		self.temperature		= 1.0

		self.memory 			= []
		self.context_window		= []

		self.zone_offset		= 8

		self._load_config()
		self.set_personality(self.personality)

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

	def get_context(self):
		return self._get_system_prompt()+self.context_window

	# Adding to context; convenience functions
	def add_to_context(self, message):
		self.context_window.append(message)
		self.context_window = self.context_window[-self.context_length:]
		last_chat = self.context_window[0]
		if last_chat['role'] == 'tool':
			# Remove both tool_calls and tool 
			self.context_window = self.context_window[1:]

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

		self.add_to_context(message)

		logger.info(f'{self.context_window[-1]['content'][0]['text']}\n')

	def add_assistant_message(self, text):
		message = {
			'role': 'assistant',
			'content': text,
		}

		self.add_to_context(message)
		logger.info(f'RotBot: {self.context_window[-1]['content']}\n')

	def add_system_message(self, text):
		message = {
			'role': 'system',
			'content': text,
		}

		self.add_to_context(message)
		logger.info(f'RotBot: {self.context_window[-1]['content']}\n')

	def add_tool_call(self, tool_call_id, tool_name, tool_arguments):
		tool_call_message = {
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
			]
		}

		self.add_to_context(tool_call_message)

	def add_tool_response(self, tool_call_id, tool_response):
		tool_call_response = {
			'role' : 'tool',
			'tool_call_id': tool_call_id,
			'content' : json.dumps(tool_response),
		}

		self.add_to_context(tool_call_response)

	# Private
	def _load_config(self):
		with open('config/base.yaml') as stream:
			yaml = YAML(typ='safe', pure=True)
			root = yaml.load(stream)
			self.model = root['model']
			self.system_prompt = root['system_prompt']
			self.temperature = root['temp']
			self.context_length = root['context_length']

	def _get_system_prompt(self):
		processed_system_prompt = self.system_prompt
		# processed_system_prompt = processed_system_prompt.replace('MEMORY', self.get_memory(chat_id))
		return [
			{
				'role': 'system', 
				'content': processed_system_prompt+self.custom_instruction
			},
		]
