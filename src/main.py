import logging

import logsafe

log_handlers = [
    logging.FileHandler("app.log"),
    logging.StreamHandler()
]
for log_handler in log_handlers:
    log_handler.setFormatter(logsafe.RedactingFormatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))

logging.basicConfig(
    level=logging.INFO,
    handlers=log_handlers
)

import asyncio
import contextlib
import openai
import telegram.error
from dotenv import load_dotenv

load_dotenv()

import access as Access
import client as Client
import assistant as Assistant
import always_on as AlwaysOn
from instance import Instance, personality_index
from models import parse_model, EFFORTS, MODELS

import db
import memories as Memories
import reminders as Reminders
import usage as Usage

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
		# The lock is held from storing the message to the end of the turn (see issue #32).
		async with instance.lock, Client.typing(instance):
			response = await Assistant.get_response(instance, message=message, raise_errors=True)
	except (openai.APIError, Usage.CapReached):
		logging.getLogger(__name__).exception(f'Could not word reminder {reminder["id"]} with the model, sending it as plain text.')
		response = f'Reminder: {reminder["description"]}'
	await Client.send_message(instance.unique_id, response)

async def on_message(instance_id, text, images=None, no_response=False):
	#TODO: add time taken here
    # The TODO below is outdated, as the current system must require an image or fallback to a model that has vision capabilities
	instance = get_instance(instance_id)
	# The lock is held from storing the message to sending the reply, so no other turn can append to the
	# context in between and the request always ends with a user message (see issue #32).
	async with instance.lock:
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
		# The reply is already in the history, so a failed send is only logged (see issue #37).
		try:
			await Client.send_message(instance.unique_id, response)
		except telegram.error.TelegramError:
			logging.getLogger(__name__).exception(f'Could not send the reply to chat {instance.unique_id}.')


# Commands, should somehow make a pipeline for defining them, maybe decorators?
async def set_instance_personality(instance_id, key):
	instance = get_instance(instance_id)
	if not key:
		names = ', '.join(personality_index)
		await Client.send_message(instance.unique_id, f'_Current personality: \'{instance.personality}\'. Available: {names}. Use /schizo <personality> to switch._')
		return
	try:
		instance.set_personality(key)
		await Client.send_message(instance.unique_id, f'_Succesfully swapped personality to \'{key}\'_')
	except (KeyError, FileNotFoundError):
		await Client.send_message(instance.unique_id, f'_The personality \'{key}\' does not exist._')

async def toggle_always_on(instance_id):
	instance = get_instance(instance_id)
	instance.always_on = not instance.always_on
	if instance.always_on:
		async with instance.lock:
			instance.add_user_message('API: Always On is now enabled. You are now allowed to respond to user messages.')
		await Client.send_message(instance.unique_id, '_Always On is now Enabled_')
	else:
		async with instance.lock:
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

# Anyone in a chat can see and remove that chat's memories. Both only touch the chat they are sent in.
async def list_memories(instance_id):
	entries, hidden = await Memories.for_prompt(instance_id, Memories.LIST_COUNT, Memories.LIST_CHARS)
	if not entries:
		await Client.send_message(instance_id, '_Nothing is saved in memory for this chat._')
		return
	lines = '\n'.join(f'{entry["id"]}. {entry["text"]}' for entry in entries)
	more = f'\n_{hidden} older memories are not shown._' if hidden else ''
	await Client.send_message(instance_id, f'_Memory of this chat (remove one with /forget <id>):_\n{lines}{more}')

async def forget_memory(instance_id, text):
	try:
		memory_id = int(text)
	except ValueError:
		await Client.send_message(instance_id, '_Usage: /forget <id>, the id is the number shown by /memory._')
		return
	if await Memories.delete(instance_id, memory_id):
		await Client.send_message(instance_id, f'_Forgot memory {memory_id}._')
	else:
		await Client.send_message(instance_id, f'_There is no memory {memory_id} in this chat._')

# Open to every allowed user, and shows only the chat it is sent in.
async def show_usage(instance_id):
	await Client.send_message(instance_id, await Usage.report(instance_id))

# Keep in sync with the commands registered in client.py.
HELP_TEXT = (
	'Commands:\n'
	'/help - show this list\n'
	'/model <model>-<effort> - set the model and reasoning effort, e.g. /model haiku-5.5-high. The effort is optional. With no argument it shows the current model\n'
	'/autoroute - toggle automatic model selection (overrides /model)\n'
	'/schizo [<personality>] - switch the personality of this chat. With no argument it lists the available ones\n'
	'/stfu - toggle always-on replies\n'
	'/memory - list what is saved in memory for this chat\n'
	'/forget <id> - delete one memory, the id is shown by /memory\n'
	'/usage - show what this chat has spent today, over the last 7 days and in total'
)

async def show_help(instance_id):
	await Client.send_message(instance_id, HELP_TEXT)

# Entry point
async def main():
	# First, so a missing or invalid allow-list stops the bot before anything is started.
	Access.load()

	# Client callbacks
	Client.show_help_callback					= show_help
	Client.on_message_callback					= on_message
	Client.set_instance_personality_callback	= set_instance_personality
	Client.toggle_always_on_callback			= toggle_always_on
	Client.set_instance_model_callback			= set_instance_model
	Client.toggle_auto_route_callback			= toggle_auto_route
	Client.list_memories_callback				= list_memories
	Client.forget_memory_callback				= forget_memory
	Client.show_usage_callback					= show_usage

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
