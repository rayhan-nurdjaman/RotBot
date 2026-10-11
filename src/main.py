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
import contextlib
import openai
from dotenv import load_dotenv

load_dotenv()

import client as Client
import assistant as Assistant
import always_on as AlwaysOn
from instance import Instance
from models import parse_model, EFFORTS, MODELS

import db
import reminders as Reminders

instances = {}

def get_instance(instance_id):
	global instances
	if instance_id not in instances:
		instances[instance_id] = Instance(instance_id)
	return instances[instance_id]

# Callbacks

# A reminder this much past its due time is called late in the prompt, so the model can say so.
LATE_AFTER_SECONDS = 120

def _late_text(seconds):
	minutes = round(seconds / 60)
	if minutes < 60:
		return f'{minutes} minutes'
	hours, minutes = divmod(minutes, 60)
	if hours < 48:
		return f'{hours} h {minutes} min'
	return f'{hours // 24} days'

# Called by the reminder scheduler. It runs a normal turn so the reminder is worded in the chat's persona, and
# raises if the reminder did not reach the chat, which makes the scheduler retry it later. If only the LLM is
# down the reminder is still sent, as plain text.
async def on_reminder(reminder, late_seconds=0):
	instance = get_instance(reminder['chat_id'])
	message = f'API: The reminder \'{reminder["description"]}\' is up. Please remind accordingly. Notify the user by tagging their username.'
	if late_seconds >= LATE_AFTER_SECONDS:
		message += f' It was due {_late_text(late_seconds)} ago (it could not be delivered earlier), so mention that it is late.'

	try:
		async with Client.typing(instance):
			# The message is stored by get_response, once it holds the chat lock (see issue #32).
			response = await Assistant.get_response(instance, message=message, raise_errors=True)
	except openai.APIError:
		logging.getLogger(__name__).exception(f'Could not word reminder {reminder["id"]} with the model, sending it as plain text.')
		response = f'Reminder: {reminder["description"]}'
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

	scheduler = None
	try:
		await db.init()
		await Client.init()
		scheduler = asyncio.create_task(Reminders.run(on_reminder))
		# Runs until the program is stopped. The scheduler never returns, so this only ends if it is cancelled or crashes.
		await scheduler
	finally:
		if scheduler:
			scheduler.cancel()
			with contextlib.suppress(asyncio.CancelledError):
				await scheduler
		await Client.shutdown()
		await db.close()

if __name__ == "__main__":
	asyncio.run(main())
