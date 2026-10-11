import os
import openai

from config import config
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

# OpenRouter speaks the OpenAI Chat Completions protocol, so the openai SDK is pointed at it.
# Optional attribution headers (HTTP-Referer, X-OpenRouter-Title) are not sent.
openai_client = openai.AsyncOpenAI(
	base_url='https://openrouter.ai/api/v1',
	api_key=os.getenv('OPENROUTER_API_KEY'),
)

# model: a models.ModelSpec (slug + reasoning effort). chat_id is the stable id used for provider-sticky
# routing (session_id) and for grouping requests in the OpenRouter logs (user).
# Claude 5.5 rejects non-default sampling parameters, so temperature is deliberately not sent.
async def create(messages, model, tools=None, chat_id=None):
	extra_body = {'reasoning': {'effort': model.effort}}
	if chat_id is not None:
		extra_body['session_id'] = str(chat_id)

	kwargs = {}
	if tools:
		kwargs['tools'] = tools
		kwargs['parallel_tool_calls'] = True
	if chat_id is not None:
		kwargs['user'] = str(chat_id)

	response = await openai_client.chat.completions.create(
		messages=messages,
		model=model.slug,
		max_tokens=config['max_tokens'],
		extra_body=extra_body,
		**kwargs,
	)
	return response.choices[0]
