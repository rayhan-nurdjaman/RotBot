import logging

import telegram
import telegram.ext

import io 
import os
import asyncio
from enum import Enum

import random # should move this to casino
import sqlite3 # Legacy, for updating db. should move this to casino

import casino as Casino
from instance import Instance

logger = logging.getLogger(__name__)

class Indicator(Enum):
	GENERATING_RESPONSE	= 0
	SAVING_MEMORY		= 1
	GENERATING_IMAGE	= 2
	SEARCHING_THE_WEB	= 3
	REMINDER_CREATED	= 4

api_key = os.getenv('TELEGRAM_API_KEY')
application = telegram.ext.Application.builder().token(api_key).build()

on_message_callback			= None
set_personality_callback	= None
toggle_always_on_callback	= None

async def init():
	await application.initialize()
	await application.updater.start_polling(drop_pending_updates=True)
	await application.start()

async def shutdown():
	await application.updater.stop()
	await application.stop()
	await application.shutdown()

async def send_message(chat_id, message, message_id=None):
	await application.bot.send_message(
		chat_id=chat_id,
		text=message,
		parse_mode=telegram.constants.ParseMode.MARKDOWN
	)

async def send_image(chat_id, image_url):
	await application.bot.send_photo(chat_id, image_url)

async def send_gif(chat_id, gif_url):
	await application.bot.send_animation(chat_id=chat_id, animation=gif_url)

async def send_indicator(instance, indicator, info):
	match indicator:
		case Indicator.GENERATING_RESPONSE: # This hopefully will be deprecated once streaming is up
			await application.bot.send_chat_action(chat_id=instance.unique_id, action=telegram.constants.ChatAction.TYPING)
		case Indicator.SAVING_MEMORY:
			await send_message(chat_id, f'RotBot remembered that \'{info}\'_')
		case Indicator.GENERATING_IMAGE:
			await send_message(chat_id, f'_Generating an image of \'{info}\'_')
		case Indicator.SEARCHING_THE_WEB:
			await send_message(chat_id, f'_Searching the web for \'{info}\'_')
		case Indicator.REMINDER_CREATED:
			await send_message(chat_id, f'_Reminder created at \'{info}\'_')

async def on_message(update, context):
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
	if not (user_message or photo_url):
		return

	# TODO: Streamline data collection
	with open('data/chat.txt', 'a') as file:
		file.write('USER: ' + user_message + '\n')

	message = f'{username}: ' + reply_prefix + user_message
	images = await self._handle_image(update, context) if update.message.photo else None

	await on_message_callback(chat_id, message, images, no_response=False)

# Private functions
def _register_handlers():
	application.add_handler(telegram.ext.CommandHandler('rollhumor', roll_for_humor))
	application.add_handler(telegram.ext.CommandHandler('penis', penis))
	application.add_handler(telegram.ext.CommandHandler('stfu', toggle_always_on))
	application.add_handler(telegram.ext.CommandHandler('coinflip', coinflip))
	application.add_handler(telegram.ext.CommandHandler('aura', aura))
	application.add_handler(telegram.ext.CommandHandler('leaderboard', leaderboard))
	application.add_handler(telegram.ext.CommandHandler('schizo', schizo))
	application.add_handler(telegram.ext.CommandHandler('sql', sql))
	application.add_handler(telegram.ext.MessageHandler(telegram.ext.filters.ALL, on_message))

# TODO: Need to figure out what happens when user sends an album
# This is mainly here to reduce bytes going in and out, as the current backend uses 512px square images
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

	file = await context.bot.get_file(chosen_photo.file_id)

	out_buffer = io.BytesIO()
	await file.download_to_memory(out_buffer)
	out_buffer.seek(0)
	data = out_buffer.read()

	return data

# Commands
async def sql(update: telegram.Update, context: telegram.ext.ContextTypes.DEFAULT_TYPE) -> None:
	command = " ".join(context.args)
	ops = ['username']
	user = update.message.from_user.username
	if user in ops:
		with sqlite3.connect("data/database.db") as con:
			cur = con.cursor()
			cur.execute(command)
			con.commit()

async def aura(update: telegram.Update, context: telegram.ext.ContextTypes.DEFAULT_TYPE) -> None:
	user_id = update.message.from_user.id
	chat_id = update.message.chat.id
	username = update.message.from_user.username
	message = f'API: User {username} used the \'Aura\' command. Their Aura balance currently has {Casino.get_balance(user_id)} Aura.'

	await get_assistant_response(chat_id, message, images=[], no_response=True)
	await update.message.reply_text(f'You currently have {Casino.get_balance(user_id)} Aura.')

async def penis(update: telegram.Update, context: telegram.ext.ContextTypes.DEFAULT_TYPE) -> None:
	user_id = update.message.from_user.id
	chat_id = update.message.chat.id
	username = update.message.from_user.username
	value = random.randint(0, 12)
	Casino.modify_balance(user_id, value * 50)

	message = f'API: User {username} used the \'Penis\' command. They have gained {value * 50} Aura.'
	await get_assistant_response(chat_id, message, images=[], no_response=True)

	await update.message.reply_text(f'8{'='*value}D')

async def schizo(update: telegram.Update, context: telegram.ext.ContextTypes.DEFAULT_TYPE) -> None:
	chat_id = update.message.chat.id
	key = context.args[0].lower()
	await set_personality_callback(chat_id, key)

async def coinflip(update: telegram.Update, context: telegram.ext.ContextTypes.DEFAULT_TYPE) -> None:
	outcomes = ['heads', 'tails']
	try:
		guess = context.args[0].lower()
		if guess not in outcomes:
			raise ValueError
		wager = int(context.args[1])
	except:
		await update.message.reply_text('Usage: /coinflip <heads/tails> <amount>')
	else:
		user_id = update.message.from_user.id
		chat_id = update.message.chat.id
		username = update.message.from_user.username
		if wager <= Casino.get_balance(user_id):
			outcome = outcomes[random.randint(0, 1)]
			message = ''
			if guess == outcome:
				# Won
				Casino.modify_balance(user_id, wager)
				message = f'API: {username} flipped a coin, wagering {wager} Aura on {guess}. The outcome was {outcome} and they won.'
				await update.message.reply_text(f'Good guess! You won {wager} Aura.')
			else:
				# Lost
				Casino.modify_balance(user_id, -wager)
				message = f'API: {username} flipped a coin, wagering {wager} Aura on {guess}. The outcome was {outcome} and they lost.'
				await update.message.reply_text(f'Tough luck! You lost {wager} Aura.')
			await get_assistant_response(chat_id, message, images=[], no_response=True)
		else:
			# Not enough
			await update.message.reply_text('Not enough Aura!')

async def leaderboard(update: telegram.Update, context: telegram.ext.ContextTypes.DEFAULT_TYPE) -> None:
	chat = update.message.chat
	chat_id = chat.id
	caller = update.message.from_user.username
	leaderboard_array = []

	for user_id, aura in Casino.get_top(5):
		# TODO: chat_id here is somehow redundant, even in different group chats it will return the user
		chat_member = await context.bot.get_chat_member(chat_id=chat_id, user_id=user_id)
		username = chat_member.user.username
		leaderboard_array.append((username, aura))

	leaderboard_string = ""
	rank = 1
	for username, balance in leaderboard_array:
		leaderboard_string += f"{rank}. {username}: {balance} Aura\n"
		rank += 1

	# Can modify string here

	if leaderboard_string:
		message = f'API: User {caller} used the \'Aura Leaderboard\' command. The current Aura leaderboard is as follows:\n{leaderboard_string}'
		await get_assistant_response(chat_id, message, images=[], no_response=True)
		await update.message.reply_text(
			leaderboard_string,
		)
	else:
		await update.message.reply_text(
			'No data available for the leaderboard.',
		)

async def roll_for_humor(update: telegram.Update, context: telegram.ext.ContextTypes.DEFAULT_TYPE) -> None:
	user_id = str(update.message.from_user.id)
	chat_id = update.message.chat.id
	# TODO: Change this to file names
	rolls = [1, 2, 3, 4, 5, 6]
	rolled = random.choice(rolls)
	self.casino.modify_balance(user_id, rolled * 100)
	await context.bot.send_video_note(chat_id=chat_id, video_note=open(f'dicerolls/{rolled}.mp4', 'rb'))
	# TODO: A bit cursed having an emoji here
	message = '💀'*rolled
	if update.message.reply_to_message:
		await update.message.reply_to_message.reply_text(message)
	else:
		await update.message.reply_text(message)

async def toggle_always_on(update: telegram.Update, context: telegram.ext.ContextTypes.DEFAULT_TYPE) -> None:
	chat_id = update.message.chat.id
	await toggle_always_on_callback(chat_id)

_register_handlers()

with open('data/chat.txt', 'a') as file:
	file.write('INIT\n')

logger.info('Client object initialized.')