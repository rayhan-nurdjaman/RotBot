import json
import random
import aiohttp
import requests

class GIFFetcher(object):
	def __init__(self, token):
		self.api_key = token

	async def _get(self, params):
		params['key'] = self.api_key
		params['client_key'] = 'RotBot'
		params['limit'] = 8
		async with aiohttp.ClientSession() as session:
			async with session.get('https://tenor.googleapis.com/v2/search', params=params) as response:
				response_text = await response.text()
				results = json.loads(response_text)
				return results

	async def search(self, tag, safesearch=False, limit=None):
		params = {'q': tag}
		results = await self._get(params)
		return results

	async def random(self, tag):
		search_results = await self.search(tag=tag)
		random_entry = random.choice(search_results['results'])
		gif = random_entry['media_formats']['gif']['url']
		return gif
