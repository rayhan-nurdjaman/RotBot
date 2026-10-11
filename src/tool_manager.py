import logging

import client as Client

from tools import time_manager as TimeManager
from tools import search_engine as SearchEngine

logger = logging.getLogger(__name__)

# Call tools
async def get_tool_response(instance, tool_name, tool_arguments):
	tool_response = None
	logger.info(f'{tool_name} tool called.')
	match tool_name:
		case 'save_to_memory':
			tool_response = instance.add_memory(tool_arguments.get('user_info'))
		case 'get_time':
			tool_response = TimeManager.get_time()
		case 'web_search':
			await Client.send_message(instance.unique_id, tool_arguments.get('idle_message'))
			tool_response = await SearchEngine.get_links(tool_arguments.get('search_term'))
		case 'get_url_text':
			await Client.send_message(instance.unique_id, tool_arguments.get('idle_message'))
			tool_response = await SearchEngine.get_text(tool_arguments.get('url'))
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

	return tool_response