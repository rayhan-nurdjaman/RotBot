import logging

import client as Client
from instance import Instance

from tools import gif_fetcher as GIFFetcher
from tools import time_manager as TimeManager
from tools import search_engine as SearchEngine
from tools import image_generation as ImageGeneration

logger = logging.getLogger(__name__)

async def get_tool_response(instance, tool_name, tool_arguments):
	tool_response = None
	match tool_name:
		case 'save_to_memory':
			# Logic for saving to memory
			pass
		case 'send_gif':
			search_term = tool_arguments.get('search_term')
			logger.info(f'RotBot tried searching for {search_term} on Tenor.')
			url = await GIFFetcher.random_gif(search_term)
			await Client.send_gif(instance.unique_id, url)
			tool_response = {
				'state' : 'GIF Sent successfully.',
				'search_term' : search_term,
			}
		case 'get_time':
			tool_response = TimeManager.get_time()
		case 'web_search':
			await Client.send_indicator(instance, Client.Indicator.SEARCHING_THE_WEB)
			tool_response = await SearchEngine.search(tool_arguments.get('search_term'), tool_arguments.get('query'))
		case 'set_reminder':
			await Client.send_indicator(instance, Client.Indicator.REMINDER_CREATED)
			tool_response = TimeManager.add_reminder(
				instance.unique_id,
				tool_arguments.get('description'),
				int(tool_arguments.get('year')),
				int(tool_arguments.get('month')),
				int(tool_arguments.get('day')),
				int(tool_arguments.get('hour')),
				int(tool_arguments.get('minute')),
				int(tool_arguments.get('second'))
			)
		case 'generate_image':
			await Client.send_indicator(instance, Client.Indicator.GENERATING_IMAGE)
			image_prompt = tool_arguments.get('prompt')
			url = await ImageGeneration.get_url(image_prompt)
			if url:
				await Client.send_image(instance.unique_id, url)
				tool_response = {'state': 'Created image successfully.'}
			else:
				tool_response = {'state': 'Failed to create image.'}
	return tool_response