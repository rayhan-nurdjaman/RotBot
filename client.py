# from logging_config import logger

import telegram
import telegram.ext

import time
import asyncio
import json
import random
import io
import os
import sqlite3

import gif_fetcher
import search_engine
from assistant import Assistant

class Client:
	def __init__(self, token, assistant, gif_fetcher, search_engine, casino, time_manager):
		# Internal things
		self.application 		= telegram.ext.Application.builder().token(token).build()
		self.assistant 			= assistant
		self.gif_fetcher 		= gif_fetcher
		self.search_engine 		= search_engine
		self.casino 			= casino
		self.time_manager 		= time_manager
		# Bot propertires
		self.WPM 				= 400
		self.poll_rate 			= 1 # Times per second
		# Initialize data
		self.enable_always_on 	= {}
		# User data
		self._register_handlers()

		with open('data/chat.txt', 'a') as file:
			file.write('INIT\n')

		print('Client object initialized.\n')

	# Public | Ideally any modification goes here
	async def run(self):
		await self.application.initialize()
		await self.application.updater.start_polling(drop_pending_updates=True)
		await self.application.start()
		try:
			while True:
				await self.poll()
				await asyncio.sleep(1.0 / self.poll_rate)
		except KeyboardInterrupt:
			print("Shutting down.\n")
			await application.updater.stop()
			await application.stop()
			await application.shutdown()
		else:
			await application.updater.stop()
			await application.stop()
			await application.shutdown()

	async def poll(self):
		await self.time_manager.poll(self.on_reminder)

	async def on_message(self, update, context):
		chat_id = str(update.message.chat.id)
		message_id = str(update.message.message_id)
		username = update.message.from_user.username
		user_message = ''
		photo_url = await self._handle_image(update, context) if update.message.photo else None

		reply_prefix = ''
		reply = update.message.reply_to_message

		# Setup reply prefix to add context
		# TODO: in topics, this is somehow always valid, causing reply.text to be always None
		if reply:
			if update.message.photo:
				reply_prefix += f'*replying to \'{reply.caption}\' by {reply.from_user.username}* '
			else:
				reply_prefix += f'*replying to \'{reply.text}\' by {reply.from_user.username}* '

		# Get the user's prompt, property differs when user sends an attachment
		if update.message.text:
			user_message = f'{update.message.text}'
		if update.message.caption:
			user_message = f'{update.message.caption}'

		if user_message or photo_url:
			with open('data/chat.txt', 'a') as file:
				file.write('USER: ' + user_message + '\n')

			entry = f'{username}: ' + reply_prefix + user_message
			self.assistant.add_user_message(chat_id, entry, photo_url)
			if self._can_talk(chat_id):
				await self._respond(chat_id, message_id=message_id)
			else:
				self.assistant.add_user_message(chat_id, 'API: Always On is currently disabled. You are not allowed to respond.')

	async def on_reminder(self, chat_id, reminder):
		message = f'API: The reminder \'{reminder['description']}\' is up. Please remind accordingly. Notify the user by tagging their username.'
		self.assistant.add_user_message(chat_id, message)
		await self._respond(chat_id)

	# Commands
	async def sql(self, update: telegram.Update, context: telegram.ext.ContextTypes.DEFAULT_TYPE) -> None:
		command = " ".join(context.args)
		ops = ['username']
		user = update.message.from_user.username
		if user in ops:
			with sqlite3.connect("data/database.db") as con:
				cur = con.cursor()
				cur.execute(command)
				con.commit()

	async def aura(self, update: telegram.Update, context: telegram.ext.ContextTypes.DEFAULT_TYPE) -> None:
		user_id = str(update.message.from_user.id)
		chat_id = str(update.message.chat.id)
		username = update.message.from_user.username
		
		self.assistant.add_user_message(chat_id, f'API: User {username} used the \'Aura\' command. Their Aura balance currently has {self.casino.get_balance(user_id)} Aura.')
		await update.message.reply_text(f'You currently have {self.casino.get_balance(user_id)} Aura.')

	async def penis(self, update: telegram.Update, context: telegram.ext.ContextTypes.DEFAULT_TYPE) -> None:
		user_id = str(update.message.from_user.id)
		chat_id = str(update.message.chat.id)
		username = update.message.from_user.username
		value = random.randint(0, 12)
		self.casino.modify_balance(user_id, value * 50)
		self.assistant.add_user_message(chat_id, f'API: User {username} used the \'Penis\' command. They have gained {value * 50} Aura.')
		await update.message.reply_text(f'8{'='*value}D')

	async def schizo(self, update: telegram.Update, context: telegram.ext.ContextTypes.DEFAULT_TYPE) -> None:
		chat_id = str(update.message.chat.id)
		key = context.args[0].lower()
		success = self.assistant.set_instance_personality(chat_id, key)
		if success:
			await update.message.reply_text(f'Succesfully swapped personality to \'{key}\'')
		else:
			await update.message.reply_text(f'The personality \'{key}\' does not exist.')

	async def coinflip(self, update: telegram.Update, context: telegram.ext.ContextTypes.DEFAULT_TYPE) -> None:
		outcomes = ['heads', 'tails']
		try:
			guess = context.args[0].lower()
			if guess not in outcomes:
				raise ValueError
			wager = int(context.args[1])
		except:
			await update.message.reply_text('Usage: /coinflip <heads/tails> <amount>')
		else:
			user_id = str(update.message.from_user.id)
			chat_id = str(update.message.chat.id)
			username = update.message.from_user.username
			if wager <= self.casino.get_balance(user_id):
				outcome = outcomes[random.randint(0, 1)]
				if guess == outcome:
					# Won
					self.casino.modify_balance(user_id, wager)
					self.assistant.add_user_message(chat_id, f'API: {username} flipped a coin, wagering {wager} Aura on {guess}. The outcome was {outcome} and they won.')
					await update.message.reply_text(f'Good guess! You won {wager} Aura.')
				else:
					# Lost
					self.casino.modify_balance(user_id, -wager)
					self.assistant.add_user_message(chat_id, f'API: {username} flipped a coin, wagering {wager} Aura on {guess}. The outcome was {outcome} and they lost.')
					await update.message.reply_text(f'Tough luck! You lost {wager} Aura.')
			else:
				# Not enough
				await update.message.reply_text('Not enough Aura!')
	
	async def leaderboard(self, update: telegram.Update, context: telegram.ext.ContextTypes.DEFAULT_TYPE) -> None:
		chat = update.message.chat
		# TODO: chat_id is almost always in str, but this implementation needs both
		chat_id = chat.id
		caller = update.message.from_user.username
		leaderboard_array = []

		for user_id, aura in self.casino.get_top(5):
			# TODO: chat_id here is somehow redundant, even in different group chats it will return the user
			chat_member = await context.bot.get_chat_member(chat_id=chat_id, user_id=int(user_id))
			username = chat_member.user.username
			leaderboard_array.append((username, aura))

		leaderboard_string = ""
		rank = 1
		for username, balance in leaderboard_array:
			leaderboard_string += f"{rank}. {username}: {balance} Aura\n"
			rank += 1

		# Can modify string here

		if leaderboard_string:
			self.assistant.add_user_message(str(chat_id), f'API: User {caller} used the \'Aura Leaderboard\' command. The current Aura leaderboard is as follows:\n{leaderboard_string}')
			await update.message.reply_text(
				leaderboard_string,
			)
		else:
			await update.message.reply_text(
				'No data available for the leaderboard.',
			)
	
	async def roll_for_humor(self, update: telegram.Update, context: telegram.ext.ContextTypes.DEFAULT_TYPE) -> None:
		user_id = str(update.message.from_user.id)
		# TODO: Change this to file names
		rolls = [1, 2, 3, 4, 5, 6]
		rolled = random.choice(rolls)
		self.casino.modify_balance(user_id, rolled * 100)
		await context.bot.send_video_note(chat_id=update.message.chat_id, video_note=open(f'dicerolls/{rolled}.mp4', 'rb'))
		# TODO: A bit cursed having an emoji here
		message = '💀'*rolled
		if update.message.reply_to_message:
			await update.message.reply_to_message.reply_text(message)
		else:
			await update.message.reply_text(message)

	async def toggle_always_on(self, update: telegram.Update, context: telegram.ext.ContextTypes.DEFAULT_TYPE) -> None:
		chat_id = str(update.message.chat.id)
		if not chat_id in self.enable_always_on:
			self.enable_always_on[chat_id] = False
		self.enable_always_on[chat_id] = not self.enable_always_on[chat_id]
		if self.enable_always_on[chat_id]:
			self.assistant.add_user_message(chat_id, 'API: Always On is now enabled. You are now allowed to respond to user messages.')
			await update.message.reply_text(f'Always On is now Enabled')
		else:
			self.assistant.add_user_message(chat_id, 'API: Always On is now disabled. You are not allowed to respond to user messages.')
			await update.message.reply_text(f'Always On is now Disabled')

	# Client Tools
	# TODO: Description criteria not strict. Assistant sometimes mistakes one reminder for someone else.
	def set_reminder(self, chat_id, description, year, month, day, hour, minute, second):
		self.time_manager.add_reminder(chat_id, description, year, month, day, hour, minute, second)

	async def send_gif(self, chat_id, search_term):
		chat_id = int(chat_id)
		# TODO: Do logging
		print(f'RotBot tried searching for {search_term} on Tenor.\n')
		url = await self.gif_fetcher.random(search_term)
		await self.application.bot.send_animation(chat_id=chat_id, animation=url)
		return {
			'search_term' : search_term,
			'state' : 'Sent GIF successfully.'
		}

	# TODO: This could be all defined within search_engine.py
	async def web_search(self, search_term):
		# TODO: Maybe have the Assistant define top?
		top = 5
		links = await self.search_engine.search(search_term)
		summaries = {}

		async def process_link(link):
			try:
				print(f'Searching {link}\n')
				raw_text = await self.search_engine.get_text(link)
				print(f'Finished searching {link}, summarizing\n')
				# TODO: Make RotBot ask GPT the question, summarising often produces too long of a response without the info we need.
				summary = await self.assistant.summarize(raw_text)
				print(f'Finished summarizing {link}\n{summary}\n')
				return link, summary
			except Exception as e:
				print(f'An unexpected error occurred while processing {link}: {e}')
				return link, 'Failed to get website'

		# Limit the number of links to `top` and process them concurrently
		tasks = [process_link(link) for link in links[:top]]
		results = await asyncio.gather(*tasks)

		# Collect results into the summaries dictionary
		for link, summary in results:
			summaries[link] = summary

		return summaries

	async def generate_image(self, prompt):
		return await self.assistant.generate_image(prompt)

	# Private functions
	def _can_talk(self, chat_id):
		if not chat_id in self.enable_always_on:
			self.enable_always_on[chat_id] = False
		return self.enable_always_on[chat_id]

	def _register_handlers(self):
		self.application.add_handler(telegram.ext.CommandHandler('rollhumor', self.roll_for_humor))
		self.application.add_handler(telegram.ext.CommandHandler('penis', self.penis))
		self.application.add_handler(telegram.ext.CommandHandler('stfu', self.toggle_always_on))
		self.application.add_handler(telegram.ext.CommandHandler('coinflip', self.coinflip))
		self.application.add_handler(telegram.ext.CommandHandler('aura', self.aura))
		self.application.add_handler(telegram.ext.CommandHandler('leaderboard', self.leaderboard))
		self.application.add_handler(telegram.ext.CommandHandler('schizo', self.schizo))
		self.application.add_handler(telegram.ext.CommandHandler('sql', self.sql))
		self.application.add_handler(telegram.ext.MessageHandler(telegram.ext.filters.ALL, self.on_message))
	
	async def _respond(self, chat_id, message_id=None): # Make sure to already add the prompt before hand
		# Initial response
		await self.application.bot.send_chat_action(chat_id=int(chat_id), action=telegram.constants.ChatAction.TYPING)
		response = await self.assistant.get_response(chat_id)

		# Model tried calling a tool
		while response.finish_reason != 'stop':
			# Properly handle tool calls here
			await self._handle_tool_call(response, chat_id, message_id=message_id)
			# Have the assistant regenerate response after calling a function
			response = await self.assistant.get_response(chat_id)

		self.assistant.add_assistant_message(chat_id, response.message.content) # Finish reason == 'stop'
		# Model thinks it shouldn't respond. TODO: Probably shouldn't put this here but meh
		with open('data/chat.txt', 'a') as file:
			file.write('ASSISTANT: ' + response.message.content + '\n')

		if 'DO_NOT_RESPOND' in response.message.content:
			return

		# Model produced text
		await self.application.bot.send_message(
			chat_id=int(chat_id),
			text=response.message.content,
			reply_to_message_id=message_id,
			parse_mode=telegram.constants.ParseMode.MARKDOWN
		)

	async def _handle_image(self, update, context):
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

		return self.assistant.encode_image(data)

	async def _handle_tool_call(self, response, chat_id, message_id=None):
		tool_call = response.message.tool_calls[0]
		tool_name = tool_call.function.name
		tool_arguments = json.loads(tool_call.function.arguments)

		tool_output = None

		if tool_name == 'save_to_memory':
			tool_output = self.assistant.add_to_memory(tool_arguments.get('user_info'), chat_id)
			# if message_id:
			# 	await self.application.bot.set_message_reaction(message_id, telegram.constants.ReactionEmoji.WRITING_HAND)
		elif tool_name == 'send_gif':
			tool_output = await self.send_gif(chat_id, tool_arguments.get('search_term'))
		elif tool_name == 'get_time':
			tool_output	= self.time_manager.get_time()
		elif tool_name == 'web_search':
			await self.application.bot.send_message(chat_id=chat_id, text=f'Googling \'{tool_arguments.get('search_term')}\'...')
			tool_output	= await self.web_search(tool_arguments.get('search_term'))
		elif tool_name == 'set_reminder':
			self.time_manager.add_reminder(
				chat_id,
				tool_arguments.get('description'),
				int(tool_arguments.get('year')),
				int(tool_arguments.get('month')),
				int(tool_arguments.get('day')),
				int(tool_arguments.get('hour')),
				int(tool_arguments.get('minute')),
				int(tool_arguments.get('second'))
			)
		elif tool_name == 'image_gen':
			image_prompt = tool_arguments.get('prompt')
			await self.application.bot.send_message(chat_id=chat_id, text=f'Generating \'{image_prompt}\'...')
			url = await self.assistant.generate_image(image_prompt)
			if url:
				await self.application.bot.send_photo(int(chat_id), url)
				tool_output = {
					'state' : 'Created image successfully.'
				}
			else:
				tool_output = {
					'state' : 'Failed to create image.'
				}

		self.assistant.add_tool_message(chat_id, tool_call, tool_output)
