import requests
import json
import random

class GIFFetcher(object):
    def __init__(self, token):
        self.api_key = token

    def _get(self, params):
        params['key'] = self.api_key
        params['client_key'] = 'RotBot'
        params['limit'] = 8
        response = requests.get('https://tenor.googleapis.com/v2/search', params=params)
        results = json.loads(response.text)
        return results

    def search(self, tag, safesearch=False, limit=None):
        params = {'q': tag}
        results = self._get(params)
        return results

    def random(self, tag):
        search_results = self.search(tag=tag)
        random_entry = random.choice(search_results['results'])
        gif = random_entry['media_formats']['gif']['url']
        return gif

    async def asearch(self, tag, safesearch=False, limit=None):
        params = {'tag': tag}
        if safesearch:
            params['safesearch'] = safesearch
        if limit:
            params['limit'] = limit
        results = self._get(**params)
        return results

    async def arandom(self, tag):
        search_results = self.search(tag=tag)
        random_entry = random.choice(search_results['results'])
        gif = random_entry['media'][0]['gif']['url']
        return gif
