import functools
import requests
import json
import random
from urllib.parse import urlencode

class TenorImage(object):
    def __init__(self, data=None):
        if data:
            self.created = data.get('created')
            self.url = data.get('url')
            self.tags = data.get('tags')
            self.type = data.get('tupe')
            self.dims = ""
            self.preview = ""
            self.size = ""
            self.duration = ""


class Tenor(object):
    def __init__(self, token):
        self.api_key = token

    def _get(self, **params):
        params['api_key'] = self.api_key

        # response = requests.get('https://tenor.googleapis.com/v2/search', params=params)
        response = requests.get(
    "https://tenor.googleapis.com/v2/search?q=%s&key=%s&client_key=%s&limit=%s" % (params["tag"], self.api_key, "RotBot",  8))

        results = json.loads(response.text)

        return results

    def search(self, tag, safesearch=False, limit=None):
        params = {'tag': tag}
        if safesearch:
            params['safesearch'] = safesearch
        if limit:
            params['limit'] = limit
        results = self._get(**params)
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
