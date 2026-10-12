import json
import logging
import os
import time

import httpx

from config import config
from models import DEFAULT_MODEL, parse_model

logger = logging.getLogger(__name__)

# Picks the model (and reasoning effort) for a turn when a chat has auto-routing enabled.
# Asks Jev, a decision model on OpenRouter, to choose between the two candidates below. Jev does not chat: it gets some
# text and a multiple-choice question and returns the chosen option with a probability for each. See
# docs/models-and-openrouter.md ("The router") before changing the question, the candidates or the state that is sent.
#
# Contract: route(instance) is awaited once per turn while the chat's lock is held, must not mutate the instance and
# returns a models.ModelSpec. It never raises: if Jev fails in any way the cheapest model is used.
JEV_MODEL	= 'typesafe/jev-1.13'
JEV_URL		= 'https://openrouter.ai/api/v1/systemone'

# Jev reads these descriptions, so they are the routing policy. Keys are ModelSpec names (see models.parse_model).
CANDIDATES = {
	'haiku-5.5-low':		'Casual chat, simple questions, short replies, simple reminders or memory requests, small lookups.',
	'sonnet-5.5-medium':	'Reading images or documents with small text, multi-step reasoning, planning, code, long or ambiguous requests, using several tools in sequence.',
}
QUESTION = 'Which model should answer the last user message in this conversation? Pick the cheapest model that can answer it well.'

# What Jev sees: the last few user and assistant messages as text, each cut to a length. Images are replaced by a marker
# (their bytes would exceed Jev's 32k token limit). Tool calls and tool results are left out.
HISTORY_MESSAGES	= 6
MAX_CHARS			= 1000

http = httpx.AsyncClient(headers={'Authorization': f'Bearer {os.getenv("OPENROUTER_API_KEY")}'})

def _text(message):
	content = message.get('content')
	if isinstance(content, str):
		return content
	parts = []
	for part in content or []:
		if part.get('type') == 'text':
			parts.append(part['text'])
		elif part.get('type') == 'image_url':
			parts.append('[image attached]')
	return ' '.join(parts)

def _state(instance):
	lines = []
	for message in instance.context_window:
		text = _text(message) if message['role'] in ('user', 'assistant') else ''
		if text:
			lines.append(f'{message["role"]}: {text[:MAX_CHARS]}')
	return '\n'.join(lines[-HISTORY_MESSAGES:])

async def route(instance):
	body = {
		'model': JEV_MODEL,
		'state': _state(instance),
		'questions': {'model': {'type': 'choice', 'instructions': QUESTION, 'criteria': CANDIDATES}},
	}
	start = time.monotonic()
	try:
		response = await http.post(JEV_URL, json=body, timeout=config['jev_timeout_s'])
		response.raise_for_status()
		data = response.json()
		answer = data['answers']['model']
		model = parse_model(answer['choice'])
		if str(model) not in CANDIDATES:
			raise ValueError(f'Jev chose \'{answer["choice"]}\', which is not a candidate.')
	except Exception as error:
		logger.warning(f'Jev routing failed ({type(error).__name__}: {error}). Using {DEFAULT_MODEL}.')
		return DEFAULT_MODEL

	# Not recorded in the usage table (see docs/models-and-openrouter.md); this line is the only record of its cost.
	logger.info('jev_call ' + json.dumps({
		'chat_id': instance.unique_id,
		'choice': str(model),
		'probabilities': answer.get('probabilities'),
		'confidence': answer.get('confidence'),
		'cost': (data.get('usage') or {}).get('cost'),
		'latency_s': round(time.monotonic() - start, 3),
	}))
	return model
