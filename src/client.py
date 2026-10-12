import logging

import telegram
import telegram.ext

import access as Access

import io
import os
import asyncio
import contextlib
from enum import Enum

logger = logging.getLogger(__name__)

class Indicator(Enum):
	GENERATING_RESPONSE	= 0
	REMINDER_CREATED	= 4

# Telegram clears a typing status after about 5 seconds, so it is re-sent a little sooner than that.
TYPING_REFRESH_SECONDS = 4

api_key = os.getenv('TELEGRAM_API_KEY')
application = telegram.ext.Application.builder().token(api_key).build()

on_message_callback					= None
toggle_always_on_callback			= None
set_instance_personality_callback	= None
set_instance_model_callback			= None
toggle_auto_route_callback			= None
list_memories_callback				= None
forget_memory_callback				= None
show_help_callback					= None

async def _handle_image(update, context):
	# Telegram lists the sizes of a photo from smallest to largest. Take the largest, assistant.encode_image caps it.
	chosen_photo = update.message.photo[-1]

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

# Telegram rejects a message over this many characters.
MESSAGE_LIMIT = 4096

# Cuts text into pieces of at most `limit` characters, preferring to cut at a blank line, then a newline, then a space.
# Only whitespace at a cut is dropped, nothing else is lost.
def split_message(text, limit=MESSAGE_LIMIT):
	chunks = []
	while len(text) > limit:
		cut = max(text.rfind('\n\n', 0, limit), 0) or max(text.rfind('\n', 0, limit), 0) or max(text.rfind(' ', 0, limit), 0)
		if cut:
			chunk, text = text[:cut], text[cut:].lstrip()
		else:
			chunk, text = text[:limit], text[limit:]
		chunks.append(chunk.rstrip() or chunk)
	chunks.append(text)
	return chunks

# Long text is sent as several messages. Each is tried as Markdown first and as plain text if Telegram rejects
# that (the reason is logged, see issue #37). An empty text is not sent: Telegram rejects it. A failure of the
# plain-text send propagates, so a caller that needs delivery (reminders) can retry.
async def send_message(chat_id, message, message_id=None):
	if not message or not message.strip():
		logger.warning(f'Not sending an empty message to chat {chat_id}.')
		return
	for chunk in split_message(message):
		try:
			await application.bot.send_message(
				chat_id=chat_id,
				text=chunk,
				parse_mode=telegram.constants.ParseMode.MARKDOWN
			)
		except telegram.error.TelegramError as e:
			logger.warning(f'Telegram rejected a Markdown message for chat {chat_id} ({type(e).__name__}: {e}), sending it as plain text.')
			await application.bot.send_message(
				chat_id=chat_id,
				text=chunk,
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

# Shows 'typing' for as long as the body runs (LLM calls and tool execution), and stops when it ends or fails.
# Reasoning text is never sent to the chat, the typing status is all the user sees while the model thinks.
@contextlib.asynccontextmanager
async def typing(instance):
	async def refresh():
		while True:
			try:
				await send_indicator(instance, Indicator.GENERATING_RESPONSE)
			except telegram.error.TelegramError:
				logger.warning('Failed to send typing indicator.', exc_info=True)
			await asyncio.sleep(TYPING_REFRESH_SECONDS)

	task = asyncio.create_task(refresh())
	try:
		yield
	finally:
		task.cancel()
		with contextlib.suppress(asyncio.CancelledError):
			await task

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
	await set_instance_personality_callback(update.message.chat.id, context.args[0].lower() if context.args else '')

async def toggle_always_on(update: telegram.Update, context: telegram.ext.ContextTypes.DEFAULT_TYPE) -> None:
	await toggle_always_on_callback(update.message.chat.id)

async def model(update: telegram.Update, context: telegram.ext.ContextTypes.DEFAULT_TYPE) -> None:
	await set_instance_model_callback(update.message.chat.id, ' '.join(context.args))

async def autoroute(update: telegram.Update, context: telegram.ext.ContextTypes.DEFAULT_TYPE) -> None:
	await toggle_auto_route_callback(update.message.chat.id)

async def memory(update: telegram.Update, context: telegram.ext.ContextTypes.DEFAULT_TYPE) -> None:
	await list_memories_callback(update.message.chat.id)

async def forget(update: telegram.Update, context: telegram.ext.ContextTypes.DEFAULT_TYPE) -> None:
	await forget_memory_callback(update.message.chat.id, ' '.join(context.args))

async def help(update: telegram.Update, context: telegram.ext.ContextTypes.DEFAULT_TYPE) -> None:
	await show_help_callback(update.message.chat.id)

# Runs before every other handler (group -1). Anyone who is not on the allow-list is dropped here: no reply, and
# nothing reaches the conversation. The log line has ids only, never the message text. An update with no sender
# (a channel post, an anonymous group admin) is dropped too. If this function itself failed, PTB would carry on
# to the other handlers, so it must not raise anything but ApplicationHandlerStop.
async def gate(update: telegram.Update, context: telegram.ext.ContextTypes.DEFAULT_TYPE) -> None:
	user = update.effective_user
	if user is not None and Access.is_allowed(user.id):
		return
	chat = update.effective_chat
	logger.warning(f'Dropped an update from a user who is not allowed: user id {user.id if user else None}, chat id {chat.id if chat else None}.')
	raise telegram.ext.ApplicationHandlerStop

# TODO(/usage): not implemented yet, see https://github.com/rayhan-nurdjaman/RotBot/issues/31
# Any member of a chat can run it, and it shows only that chat's own usage and spend.

application.add_handler(telegram.ext.TypeHandler(telegram.Update, gate), group=-1)
application.add_handler(telegram.ext.CommandHandler('help', help))
application.add_handler(telegram.ext.CommandHandler('stfu', toggle_always_on))
application.add_handler(telegram.ext.CommandHandler('model', model))
application.add_handler(telegram.ext.CommandHandler('autoroute', autoroute))
application.add_handler(telegram.ext.CommandHandler('schizo', schizo))
application.add_handler(telegram.ext.CommandHandler('memory', memory))
application.add_handler(telegram.ext.CommandHandler('forget', forget))
application.add_handler(telegram.ext.MessageHandler(telegram.ext.filters.ALL, on_message))

logger.info('Client object initialized.')
