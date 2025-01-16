import os
import json
import openai
# Tool returns should be standardised to JSON

# Message formats here follow OpenAI's style

# Example of a user message(content here is also applicable for assistant and system). 

'''
{
	'role': 'user',
	'content': [
		{
			'type': 'text',
			'text': text
		}
		{
			'type': 'image_url',
			'image_url': {
				'url': image_url,
				"detail": "low"
			}
		}
	]
}
'''

# Example of tool call

'''
{
	'content' : [] # Will be empty since opted for tool call.
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
'''

# Example of tool response

'''
{
	'role' : 'tool',
	'tool_call_id': tool_call.id, # Refer to tool call
	'content' : json.dumps(tool_output),
}
'''

class CompletionStream:
	def __init__(self):
		pass

api_key = None

openai_client = openai.AsyncOpenAI(api_key=os.getenv('OPENAI_API_KEY'))

async def create(messages, model, tools=None, temp=1.0):
	if tools:
		response = await openai_client.chat.completions.create(
			messages=messages,
			model=model,
			temperature=temp,
			tools=tools,
			parallel_tool_calls=True,
			#stream=True
		)
		return response.choices[0]
	else:
		response = await openai_client.chat.completions.create(
			messages=messages,
			model=model,
			temperature=temp,
			# tools=tools,
			# parallel_tool_calls=False,
			#stream=True
		)
		return response.choices[0]
