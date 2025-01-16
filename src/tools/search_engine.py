# AI powered search
import os
import aiohttp
import asyncio
import logging
from bs4 import BeautifulSoup
import chat_completion as ChatCompletion

api_key = os.getenv('GOOGLE_API_KEY')
search_engine_id = os.getenv('GOOGLE_SEARCH_ENGINE_ID')

logger = logging.getLogger(__name__)

summarize = False

async def get_links(search_term):
	params = {
		'key': api_key,
		'cx': search_engine_id,
		'q': search_term
	}
	async with aiohttp.ClientSession() as session:
		async with session.get("https://customsearch.googleapis.com/customsearch/v1", params=params) as response:
			search_results = await response.json()
			res = [[item['title'], item['link'], item['snippet']] for item in search_results.get('items', [])]
			return res

async def get_text(url, query=None):
	# TODO: add outbound links
	headers = {
		"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3"
	}
	async with aiohttp.ClientSession() as session:
		async with session.get(url, headers=headers) as response:
			html_content = await response.text()
			soup = BeautifulSoup(html_content, 'html.parser')
			text = soup.get_text()
			if query and summarize:
				system_prompt = [
					{
						'role' : 'system',
						'content' : [
							{
								'type' : 'text',
								'text' : 'Your role is to analyze the content of a website and provide a concise and clear summary based on a specific query. Focus only on information relevant to the query while ignoring irrelevant details or extraneous text.'
							}
						]
					}
				]
				logger.info(f'Querying AI on {link}')
				user_prompt = [
					{
						'role' : 'user',
						'content' : [
							{
								'type' : 'text',
								'text' : f'Using the provided query and the website content, generate a summary:\nQuery: {query}\nWebsite Content: {text}'
							}
						]
					}
				]
				completion = await ChatCompletion.create(system_prompt+user_prompt, 'gpt-4o-mini')
				text = completion.message.content
			return text


# async def search(search_term, query):
# 	logger.info(f'search_term={search_term}; query={query}')
# 	links = await get_links(search_term)
# 	n = 3
# 	summaries = {}
# 	summarize = True
# 	# Helper function
# 	async def process_link(link):
# 		try:
# 			logger.info(f'Getting raw text from {link}')
# 			raw_text = await get_text(link)
# 			text = ''
# 			if summarize:
# 				system_prompt = [
# 					{
# 						'role' : 'system',
# 						'content' : [
# 							{
# 								'type' : 'text',
# 								'text' : 'Your role is to analyze the content of a website and provide a concise and clear summary based on a specific query. Focus only on information relevant to the query while ignoring irrelevant details or extraneous text.'
# 							}
# 						]
# 					}
# 				]
# 				logger.info(f'Querying AI on {link}')
# 				user_prompt = [
# 					{
# 						'role' : 'user',
# 						'content' : [
# 							{
# 								'type' : 'text',
# 								'text' : f'Using the provided query and the website content, generate a summary:\nQuery: {query}\nWebsite Content: {raw_text}'
# 							}
# 						]
# 					}
# 				]
# 				completion = await ChatCompletion.create(system_prompt+user_prompt, 'gpt-4o-mini')
# 				text = completion.message.content
# 			else:
# 				text = raw_text
# 			return link, text
# 		except Exception as e:
# 			logger.error(f'An unexpected error occurred while processing {link}: {e}')
# 			return link, 'Failed to get website'

# 	tasks = [process_link(link) for link in links[:n]]
# 	results = await asyncio.gather(*tasks)

# 	for link, summary in results:
# 		summaries[link] = summary

# 	return summaries