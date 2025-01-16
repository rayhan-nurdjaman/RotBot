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
import data_collection as DataCollection
from instance import Instance

from tools import time_manager as TimeManager
from tools import search_engine as SearchEngine
from tools import image_generation as ImageGeneration

# Should instances be here?
# Maybe an instance manager singleton?
instances = {}

def get_instance(instance_id):
	global instances
	instance = instances.setdefault(instance_id, Instance(instance_id))
	return instance

# Callbacks
async def on_reminder(instance_id, reminder):
	message = f'API: The reminder \'{reminder['description']}\' is up. Please remind accordingly. Notify the user by tagging their username.'
	instance = get_instance(instance_id)
	instance.add_user_message(message)

	await Client.send_indicator(instance, Client.Indicator.GENERATING_RESPONSE)
	response = await Assistant.get_response(instance)
	await Client.send_message(instance.unique_id, response)

async def on_message(instance_id, text, images=[], no_response=False):
	DataCollection.add_user_message(text)

	instance = get_instance(instance_id)
	instance.add_user_message(text)

	if no_response:
		return

	if not instance.always_on:
		return

	await Client.send_indicator(instance, Client.Indicator.GENERATING_RESPONSE)
	response = await Assistant.get_response(instance)
	await Client.send_message(instance.unique_id, response)
	DataCollection.add_assistant_message(response)

# Commands, should somehow make a pipeline for defining them
async def set_instance_personality(instance_id, key):
	instance = get_instance(instance_id)
	try:
		instance.set_personality(key)
		await Client.send_message(instance.unique_id, f'_Succesfully swapped personality to \'{key}\'_')
	except:
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

# Entry point
async def main():
	# Client callbacks
	Client.on_message_callback					= on_message
	Client.set_instance_personality_callback	= set_instance_personality
	Client.toggle_always_on_callback			= toggle_always_on

	poll_rate = 1
	try:
		await Client.init()
		while True: #TODO: Stupid
			await TimeManager.poll(on_reminder)
			await asyncio.sleep(1.0 / poll_rate)
	finally:
		await Client.shutdown()

if __name__ == "__main__":
	asyncio.run(main())
