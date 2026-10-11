import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("app.log"),
        logging.StreamHandler()
    ]
)

import asyncio
from dotenv import load_dotenv

load_dotenv()

import client as Client
import assistant as Assistant
import always_on as AlwaysOn
from instance import Instance
from models import parse_model, EFFORTS, MODELS

from tools import time_manager as TimeManager

instances = {}

def get_instance(instance_id):
	global instances
	if instance_id not in instances:
		instances[instance_id] = Instance(instance_id)
	return instances[instance_id]

# Callbacks
async def on_reminder(instance_id, reminder):
	message = f'API: The reminder \'{reminder['description']}\' is up. Please remind accordingly. Notify the user by tagging their username.'
	instance = get_instance(instance_id)
	instance.add_user_message(message)

	async with Client.typing(instance):
		response = await Assistant.get_response(instance)
	await Client.send_message(instance.unique_id, response)

async def on_message(instance_id, text, images=None, no_response=False):
	#TODO: add time taken here
    # The TODO below is outdated, as the current system must require an image or fallback to a model that has vision capabilities
	instance = get_instance(instance_id)
	if images:  # TODO: and image modality enabled
		instance.add_user_message(text, Assistant.encode_image(images))
	else:
		instance.add_user_message(text)

	if no_response:
		return

	if not instance.always_on:
		return

	if not await AlwaysOn.should_respond(instance.get_context()):
		return

	async with Client.typing(instance):
		response = await Assistant.get_response(instance)
	await Client.send_message(instance.unique_id, response)


# Commands, should somehow make a pipeline for defining them, maybe decorators?
async def set_instance_personality(instance_id, key):
	instance = get_instance(instance_id)
	try:
		instance.set_personality('base')
		instance.set_personality(key)
		await Client.send_message(instance.unique_id, f'_Succesfully swapped personality to \'{key}\'_')
	except (KeyError, FileNotFoundError):
		await Client.send_message(instance.unique_id, f'_The personality \'{key}\' does not exist._')

async def toggle_always_on(instance_id):
	instance = get_instance(instance_id)
	instance.always_on = not instance.always_on
	if instance.always_on:
		instance.add_user_message('API: Always On is now enabled. You are now allowed to respond to user messages.')
		await Client.send_message(instance.unique_id, '_Always On is now Enabled_')
	else:
		instance.add_user_message('API: Always On is now disabled. You are not allowed to respond to user messages.')
		await Client.send_message(instance.unique_id, '_Always On is now Disabled_')

# Model is chosen with /model, e.g. 'haiku-5.5-high'. An empty argument reports the current one.
async def set_instance_model(instance_id, text):
	instance = get_instance(instance_id)
	if not text:
		await Client.send_message(instance.unique_id, f'_Current model: \'{instance.model}\'_')
		return
	try:
		instance.model = parse_model(text)
	except ValueError:
		models = ', '.join(MODELS)
		await Client.send_message(instance.unique_id, f'_Unknown model \'{text}\'. Use <model> or <model>-<effort>. Models: {models}. Efforts: {", ".join(EFFORTS)}._')
		return
	note = ' Auto routing is on and overrides this.' if instance.auto_route else ''
	await Client.send_message(instance.unique_id, f'_Model set to \'{instance.model}\'.{note}_')

async def toggle_auto_route(instance_id):
	instance = get_instance(instance_id)
	instance.auto_route = not instance.auto_route
	if instance.auto_route:
		await Client.send_message(instance.unique_id, '_Auto routing is now Enabled, it overrides /model_')
	else:
		await Client.send_message(instance.unique_id, f'_Auto routing is now Disabled, using \'{instance.model}\'_')

# Entry point
async def main():
	# Client callbacks
	Client.on_message_callback					= on_message
	Client.set_instance_personality_callback	= set_instance_personality
	Client.toggle_always_on_callback			= toggle_always_on
	Client.set_instance_model_callback			= set_instance_model
	Client.toggle_auto_route_callback			= toggle_auto_route

	poll_rate = 1
	try:
		await Client.init()
		while True:
			await TimeManager.poll(on_reminder)
			await asyncio.sleep(1.0 / poll_rate)
	finally:
		await Client.shutdown()

if __name__ == "__main__":
	asyncio.run(main())
