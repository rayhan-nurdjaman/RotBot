import os
import json
import random
import aiohttp

api_key = os.getenv('GOOGLE_API_KEY')

async def _get(params):
	params['key'] = api_key
	params['client_key'] = 'RotBot'
	params['limit'] = 8
	async with aiohttp.ClientSession() as session:
		async with session.get('https://tenor.googleapis.com/v2/search', params=params) as response:
			response_text = await response.text()
			results = json.loads(response_text)
			return results

async def search(tag, safesearch=False, limit=None):
	params = {'q': tag}
	results = await _get(params)
	return results

async def random_gif(tag):
	search_results = await search(tag=tag)
	random_entry = random.choice(search_results['results'])
	gif = random_entry['media_formats']['gif']['url']
	return gif
