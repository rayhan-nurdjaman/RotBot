import logging

import telegram
import telegram.ext

import io
import os
from enum import Enum

logger = logging.getLogger(__name__)

class Indicator(Enum):
	GENERATING_RESPONSE	= 0
	SAVING_MEMORY		= 1
	GENERATING_IMAGE	= 2
	SEARCHING_THE_WEB	= 3
	REMINDER_CREATED	= 4

api_key = os.getenv('TELEGRAM_API_KEY')
application = telegram.ext.Application.builder().token(api_key).build()

on_message_callback					= None
toggle_always_on_callback			= None
set_instance_personality_callback	= None

async def _handle_image(update, context):
	image_max_res = 512
	chosen_photo = update.message.photo[-1]
	for photo_size in update.message.photo[-2::-1]:
		width = photo_size.width
		height = photo_size.height
		min_res = min(width, height)
		if min_res > image_max_res:
			chosen_photo = photo_size
		else:
			break

	try:
		file = await context.bot.get_file(chosen_photo.file_id)

		out_buffer = io.BytesIO()
		await file.download_to_memory(out_buffer)
		out_buffer.seek(0)
		data = out_buffer.read()
	except telegram.error.TelegramError:
		logger.exception('Failed to download photo.')
		return None

	return data

async def init():
	await application.initialize()
	await application.updater.start_polling(drop_pending_updates=True)
	await application.start()

async def shutdown():
	await application.updater.stop()
	await application.stop()
	await application.shutdown()

async def send_message(chat_id, message, message_id=None):
	try:
		await application.bot.send_message(
			chat_id=chat_id,
			text=message,
			parse_mode=telegram.constants.ParseMode.MARKDOWN
		)
	except:
		await application.bot.send_message(
			chat_id=chat_id,
			text=message,
		)

async def send_client_message(chat_id, message, message_id=None):
	await send_message(chat_id, f'_{message}_')

async def send_image(chat_id, image_url):
	await application.bot.send_photo(chat_id, image_url)

async def send_gif(chat_id, gif_url):
	await application.bot.send_animation(chat_id=chat_id, animation=gif_url)

async def send_indicator(instance, indicator):
	match indicator:
		case Indicator.GENERATING_RESPONSE: # This hopefully will be deprecated once streaming is up
			await application.bot.send_chat_action(chat_id=instance.unique_id, action=telegram.constants.ChatAction.TYPING)

async def on_message(update, context):
	# Ignore updates without a message (edits, channel posts, etc.)
	if update.message is None:
		return

	chat_id			= update.message.chat.id
	message_id		= update.message.message_id
	username		= update.message.from_user.username

	# Handle replies
	reply_prefix = ''
	reply = update.message.reply_to_message
	if reply:
		# Get reply prompt, property differs when user sends an attachment
		if update.message.photo:
			reply_prefix += f'*replying to \'{reply.caption}\' by {reply.from_user.username}* '
		else:
			reply_prefix += f'*replying to \'{reply.text}\' by {reply.from_user.username}* '

	# Get the user's prompt, property differs when user sends an attachment
	user_message	= ''
	if update.message.text:
		user_message = f'{update.message.text}'
	if update.message.caption:
		user_message = f'{update.message.caption}'

	# Don't continue if user provides nothing. Ideally this shouldn't even be here. Change with Update filters.
	if not (user_message or update.message.photo):
		return

	message = f'{username}: ' + reply_prefix + user_message
	images = await _handle_image(update, context) if update.message.photo else None
	if not (user_message or images):
		return

	await on_message_callback(chat_id, message, images, no_response=False)

# Commands
async def schizo(update: telegram.Update, context: telegram.ext.ContextTypes.DEFAULT_TYPE) -> None:
	try:
		await set_instance_personality_callback(update.message.chat.id, context.args[0].lower())
	except IndexError:
		await send_client_message(update.message.chat.id, 'Usage: /schizo <personality>')

async def toggle_always_on(update: telegram.Update, context: telegram.ext.ContextTypes.DEFAULT_TYPE) -> None:
	await toggle_always_on_callback(update.message.chat.id)

application.add_handler(telegram.ext.CommandHandler('stfu', toggle_always_on))
application.add_handler(telegram.ext.CommandHandler('schizo', schizo))
application.add_handler(telegram.ext.MessageHandler(telegram.ext.filters.ALL, on_message))

logger.info('Client object initialized.')
