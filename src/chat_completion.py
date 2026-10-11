import os
import json
import time
import logging
import openai

from config import config

logger = logging.getLogger(__name__)
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
	timeout=config['llm_timeout_s'],
	max_retries=config['llm_max_retries'],
)

# model: a models.ModelSpec (slug + reasoning effort). chat_id is the stable id used for provider-sticky
# routing (session_id) and for grouping requests in the OpenRouter logs (user).
# Claude 5.5 rejects non-default sampling parameters, so temperature is deliberately not sent.
# tool_choice: only 'none' is used. It forces a text answer while keeping the tool list in the request, because
# removing tools would change the front of the prompt and invalidate the cache.
async def create(messages, model, tools=None, chat_id=None, tool_choice=None):
	extra_body = {'reasoning': {'effort': model.effort}}
	if chat_id is not None:
		extra_body['session_id'] = str(chat_id)

	kwargs = {}
	if tools:
		kwargs['tools'] = tools
		kwargs['parallel_tool_calls'] = True
		if tool_choice:
			kwargs['tool_choice'] = tool_choice
	if chat_id is not None:
		kwargs['user'] = str(chat_id)

	start = time.monotonic()
	response = await openai_client.chat.completions.create(
		messages=messages,
		model=model.slug,
		max_tokens=config['max_tokens'],
		extra_body=extra_body,
		**kwargs,
	)
	choice = response.choices[0]
	_log_call(chat_id, model, choice, response.usage, time.monotonic() - start)
	return choice

# One JSON line per LLM call, prefixed 'llm_call', so spend and caching can be grepped out of app.log.
# Token counts and cost come from OpenRouter's usage object; any of them may be missing.
def _log_call(chat_id, model, choice, usage, latency):
	completion_details = getattr(usage, 'completion_tokens_details', None)
	prompt_details = getattr(usage, 'prompt_tokens_details', None)
	tool_calls = choice.message.tool_calls or []
	logger.info('llm_call ' + json.dumps({
		'chat_id': chat_id,
		'model': model.slug,
		'effort': model.effort,
		'prompt_tokens': getattr(usage, 'prompt_tokens', None),
		'completion_tokens': getattr(usage, 'completion_tokens', None),
		'reasoning_tokens': getattr(completion_details, 'reasoning_tokens', None),
		'cached_tokens': getattr(prompt_details, 'cached_tokens', None),
		'cost': getattr(usage, 'cost', None),
		'latency_s': round(latency, 3),
		'tool_calls': [tool_call.function.name for tool_call in tool_calls],
		'finish_reason': choice.finish_reason,
	}))
