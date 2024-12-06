import requests
import asyncio
import aiohttp
from bs4 import BeautifulSoup

class SearchEngine:
	def __init__(self, api_key, search_engine_id):
		self.api_key = api_key
		self.search_engine_id = search_engine_id

	def search(self, search_term):
		params = {
			'key': self.api_key,
			'cx': self.search_engine_id,
			'q': search_term
		}
		search_results = requests.get("https://customsearch.googleapis.com/customsearch/v1", params=params).json()
		res = [item['link'] for item in search_results['items']]
		return res

	async def get_text(self, url):
		async with aiohttp.ClientSession() as session:
			async with session.get(url) as response:
				html_content = await response.text()
				soup = BeautifulSoup(html_content, 'html.parser')
				return soup.get_text()
