import requests
from bs4 import BeautifulSoup

class SearchEngine:
	def __init__(self, api_key, search_engine_id):
		self.api_key = api_key
		self.search_engine_id = search_engine_id

	def search(self, search_term, top=4):
		params = {
			'key': self.api_key,
			'cx': self.search_engine_id,
			'q': search_term
		}
		search_results = requests.get("https://customsearch.googleapis.com/customsearch/v1", params=params).json()
		raw_dumps = {}
		for i in range(min(top, len(search_results['items']))):
			item = search_results['items'][i]
			print(f'Searching {item['link']}\n')
			response = requests.get(item['link'])
			html_content = response.text
			soup = BeautifulSoup(html_content, 'html.parser')
			raw_dumps[item['link']] = soup.get_text()
		return raw_dumps
