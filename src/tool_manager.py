import logging

import client as Client
from instance import Instance

from tools import gif_fetcher as GIFFetcher
from tools import time_manager as TimeManager
from tools import search_engine as SearchEngine
from tools import image_generation as ImageGeneration

logger = logging.getLogger(__name__)

# Call tools
async def get_tool_response(instance, tool_name, tool_arguments):
	tool_response = None
	logger.info(f'{tool_name} tool called.')
	match tool_name:
		case 'save_to_memory':
			# Logic for saving to memory
			tool_output = self.assistant.add_to_memory(tool_arguments.get('user_info'), chat_id)
			instance.memory.append
			tool_output = {
				'info' : content,
				'state' : 'Information successfully saved.'
			}
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
			await Client.send_message(instance.unique_id, tool_arguments.get('idle_message'))
			tool_response = await SearchEngine.get_links(tool_arguments.get('search_term'))
		case 'get_url_text':
			await Client.send_message(instance.unique_id, tool_arguments.get('idle_message'))
			tool_response = {
				'text' : await SearchEngine.get_text(tool_arguments.get('url'))
			}
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
			await Client.send_message(instance.unique_id, tool_arguments.get('idle_message'))
			image_prompt = tool_arguments.get('prompt')
			url = await ImageGeneration.get_url(image_prompt)
			if url:
				await Client.send_image(instance.unique_id, url)
				tool_response = {'state': 'Created and sent image successfully.'}
			else:
				tool_response = {'state': 'Failed to create image.'}
	return tool_response