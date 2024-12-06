# from logging_config import logger

import telegram
import telegram.ext

import time
import asyncio
import json
import random
import io
import os

import gif_fetcher
import search_engine
import assistant

from datetime import datetime, timezone, timedelta

class Client:
	def __init__(self, token, assistant, gif_fetcher, search_engine):
		# Internal things
		self.application = telegram.ext.Application.builder().token(token).build()
		self.assistant = assistant
		self.gif_fetcher = gif_fetcher
		self.search_engine = search_engine
		# Bot propertires
		self.WPM = 400
		self.poll_rate = 1 # Times per second
		self.zone_offset = 8 # Singapore
		# Initialize data
		self.enable_always_on = {}
		self.reminders = {}
		# User data
		if not os.path.exists('data'):
			os.makedirs('data')
		try:
			with open('data/aura_balances.json', 'r') as balances_file:
				raw_aura_balances = balances_file.read()
				self.aura_balances = json.loads(raw_aura_balances) if raw_aura_balances else {}
		except FileNotFoundError:
			with open('data/aura_balances.json', 'w') as balances_file:
				balances_file.write(json.dumps({}))
				self.aura_balances = {}
		self._register_handlers()
		print('Client object initialized.\n')

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

	async def on_message(self, update, context):
		chat_id = str(update.message.chat.id)
		message_id = str(update.message.message_id)
		username = update.message.from_user.username
		user_message = ''
		photo_url = await self._handle_image(update, context) if update.message.photo else None

		reply_prefix = ''
		reply = update.message.reply_to_message

		# Setup reply prefix to add context
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
			entry = f'{username}: ' + reply_prefix + user_message
			self.assistant.add_user_message(chat_id, entry, photo_url)
			if self.can_talk(chat_id) and await self.assistant.is_user_addressing(chat_id):
				await self.respond(chat_id, message_id=message_id)

	async def poll(self):
		await self._handle_reminders()

	# Helper functions
	async def respond(self, chat_id, message_id=None): # Make sure to already add the prompt before hand
		# Initial response
		response = await self.assistant.get_response(chat_id)

		# Model tried calling a tool
		while response.finish_reason != 'stop':
			# Properly handle tool calls here
			await self._handle_tool_call(response, chat_id, message_id=message_id)
			# Have the assistant regenerate response after calling a function
			response = await self.assistant.get_response(chat_id)

		self.assistant.add_assistant_message(chat_id, response) # Finish reason == 'stop'

		chat_bubbles = response.message.content.splitlines()

		for i in range(len(chat_bubbles)):
			bubble = chat_bubbles[i]
			if bubble and not bubble.isspace():
				await self.application.bot.send_chat_action(chat_id=int(chat_id), action=telegram.constants.ChatAction.TYPING)
				if i == 0 and message_id:
					await self.application.bot.send_message(
						chat_id=int(chat_id),
						text=bubble,
						reply_to_message_id=message_id
					)
				else:
					await asyncio.sleep(min(len(bubble)/5 / (self.WPM/60), 5.0))
					await self.application.bot.send_message(chat_id=int(chat_id), text=bubble)

	def add_to_balance(self, user_id, amount):
		if not user_id in self.aura_balances:
			self.aura_balances[user_id] = 0
		self.aura_balances[user_id] += amount
		self.dump()

	def get_balance(self, user_id):
		if not user_id in self.aura_balances:
			self.aura_balances[user_id] = 0
		return self.aura_balances[user_id]

	def can_talk(self, chat_id):
		if not chat_id in self.enable_always_on:
			self.enable_always_on[chat_id] = False
		return self.enable_always_on[chat_id]

	def dump(self) -> None:
		with open('data/aura_balances.json', 'w') as file:
			file.write(json.dumps(self.aura_balances, indent=4))

	# Private functions
	def _register_handlers(self):
		self.application.add_handler(telegram.ext.CommandHandler('rollhumor', self.roll_for_humor))
		self.application.add_handler(telegram.ext.CommandHandler('penis', self.penis))
		self.application.add_handler(telegram.ext.CommandHandler('stfu', self.toggle_always_on))
		self.application.add_handler(telegram.ext.CommandHandler('coinflip', self.coinflip))
		self.application.add_handler(telegram.ext.CommandHandler('aura', self.aura))
		self.application.add_handler(telegram.ext.CommandHandler('leaderboard', self.leaderboard))
		self.application.add_handler(telegram.ext.MessageHandler(telegram.ext.filters.ALL, self.on_message))
	
	async def _handle_image(self, update, context):
		file = await context.bot.get_file(update.message.photo[-1].file_id)

		out_buffer = io.BytesIO()
		await file.download_to_memory(out_buffer)
		out_buffer.seek(0)
		data = out_buffer.read()

		return await self.assistant.encode_image(data)

	async def _handle_tool_call(self, response, chat_id, message_id=None):
		tool_call = response.message.tool_calls[0]
		tool_name = tool_call.function.name
		tool_arguments = json.loads(tool_call.function.arguments)

		tool_output = None

		if tool_name == 'save_to_memory':
			tool_output = self.assistant.add_to_memory(tool_arguments.get('user_info'), chat_id)
			if message_id:
				await self.application.bot.set_message_reaction(telegram.constants.ReactionEmoji.WRITING_HAND)
		elif tool_name == 'send_gif':
			tool_output = await self.send_gif(chat_id, tool_arguments.get('search_term'))
		elif tool_name == 'get_time':
			tool_output	= self.get_time()
		elif tool_name == 'web_search':
			await self.application.bot.send_message(chat_id=chat_id, text=f'Googling \'{tool_arguments.get('search_term')}\'...')
			tool_output	= await self.web_search(tool_arguments.get('search_term'))
		elif tool_name == 'set_reminder':
			tool_output = self.set_reminder(
				chat_id,
				tool_arguments.get('description'),
				int(tool_arguments.get('year')),
				int(tool_arguments.get('month')),
				int(tool_arguments.get('day')),
				int(tool_arguments.get('hour')),
				int(tool_arguments.get('minute')),
				int(tool_arguments.get('second'))
			)

		self.assistant.add_tool_message(chat_id, tool_call, tool_output)

	async def _handle_reminders(self):
		# Get current utc unix time 
		now = datetime.now(timezone.utc)
		current_unix_time = int(now.timestamp())
		# Seperated per chat conversation
		for chat_id in self.reminders:
			for i in range(len(self.reminders[chat_id]) - 1, -1, -1):
				reminder = self.reminders[chat_id][i]
				if reminder['unix_time'] < current_unix_time:
					system_prompt = f'The reminder \'{reminder['description']}\' is up. Please remind accordingly. Notify the user by tagging their username.'
					self.assistant.add_system_message(chat_id, system_prompt)
					self.reminders[chat_id].pop(i)
					await self.respond(chat_id)

	# Client Tools
	async def send_gif(self, chat_id, search_term):
		chat_id = int(chat_id)
		print(f'RotBot tried searching for {search_term} on Tenor.\n')
		url = self.gif_fetcher.random(search_term)
		await self.application.bot.send_animation(chat_id=chat_id, animation=url)
		return {
			'search_term' : search_term,
			'state' : 'Sent GIF successfully.'
		}

	def get_time(self):
		now = datetime.now(timezone.utc)
		offset_time = now + timedelta(hours=self.zone_offset)

		day_name = offset_time.strftime("%A")
		datetime_with_real_time = datetime.combine(offset_time.date(), offset_time.time())

		return {
			'time' : str(datetime_with_real_time),
			'day' : day_name
		}	

	async def web_search(self, search_term):
		top = 5
		links = self.search_engine.search(search_term)
		summaries = {}

		async def process_link(link):
			print(f'Searching {link}\n')
			raw_text = await self.search_engine.get_text(link)
			print(f'Finished searching {link}, summarizing\n')
			summary = await self.assistant.summarize(raw_text)
			print(f'Finished summarizing {link}\n{summary}\n')
			return link, summary

		# Limit the number of links to `top` and process them concurrently
		tasks = [process_link(link) for link in links[:top]]
		results = await asyncio.gather(*tasks)

		# Collect results into the summaries dictionary
		for link, summary in results:
			summaries[link] = summary

		return summaries

	def set_reminder(self, chat_id, description, year, month, day, hour, minute, second):
		try:
			# Sanitize input
			dt = datetime(
				year,
				month,
				day,
				hour,
				minute,
				second
			)
		except:
			return {
				'state' : 'Failed creating reminder because of invalid parameters.'
			}
		else:
			utc_offset = timezone(timedelta(hours=self.zone_offset))  # Define UTC+8 timezone
			dt = dt.replace(tzinfo=utc_offset)  # Set the timezone to UTC+8
			dt_utc = dt.astimezone(timezone.utc)
			now_utc = datetime.now(timezone.utc)
			delta = dt_utc - now_utc

			datetime_with_real_time = datetime.combine(dt_utc.date(), dt_utc.time())
			print(str(datetime_with_real_time), tool_arguments.get('description'))
		
			unix_time = int(dt_utc.timestamp())

			if not chat_id in self.reminders:
				self.reminders[chat_id] = []
			self.reminders[chat_id].append(
				{
					'unix_time' : unix_time,
					'description' : description
				}
			)
			return {
				'state' : f'Reminder \'{description}\' successfully created',
				'when' : str(dt),
				'time_to_reminder' : str(delta)
			}

	# Commands
	async def roll_for_humor(self, update: telegram.Update, context: telegram.ext.ContextTypes.DEFAULT_TYPE) -> None:
		user_id = str(update.message.from_user.id)
		rolls = [1, 2, 3, 4, 5, 6]
		rolled = random.choice(rolls)
		self.add_to_balance(user_id, rolled * 100)
		await context.bot.send_video_note(chat_id=update.message.chat_id, video_note=open(f'dicerolls/{rolled}.mp4', 'rb'))
		message = '💀'*rolled
		if update.message.reply_to_message:
			await update.message.reply_to_message.reply_text(message)
		else:
			await update.message.reply_text(message)

	async def penis(self, update: telegram.Update, context: telegram.ext.ContextTypes.DEFAULT_TYPE) -> None:
		user_id = str(update.message.from_user.id)
		value = random.randint(0, 12)
		self.add_to_balance(user_id, value * 50)
		await update.message.reply_text(f'8{'='*value}D')

	async def toggle_always_on(self, update: telegram.Update, context: telegram.ext.ContextTypes.DEFAULT_TYPE) -> None:
		chat_id = str(update.message.chat.id)
		if not chat_id in self.enable_always_on:
			self.enable_always_on[chat_id] = False
		self.enable_always_on[chat_id] = not self.enable_always_on[chat_id]
		await update.message.reply_text(f'Always on is now {'Enabled' if self.enable_always_on[chat_id] else 'Disabled'}')

	async def coinflip(self, update: telegram.Update, context: telegram.ext.ContextTypes.DEFAULT_TYPE) -> None:
		try:
			guess = context.args[0].lower()
			if guess not in ['heads', 'tails']:
				raise ValueError
			wager = int(context.args[1])
		except:
			await update.message.reply_text('Usage: /coinflip <heads/tails> <amount>')
		else:
			user_id = str(update.message.from_user.id)
			if wager <= self.aura_balances.get(user_id, 0):
				outcome = 'heads' if random.randint(0, 1) else 'tails'
				if guess == outcome:
					# Won
					self.add_to_balance(user_id, wager)
					await update.message.reply_text(f'Good guess! You won {wager} Aura.')
				else:
					# Lost
					self.add_to_balance(user_id, -wager)
					await update.message.reply_text(f'Tough luck! You lost {wager} Aura.')
			else:
				# Not enough
				await update.message.reply_text('Not enough Aura!')
	
	async def aura(self, update: telegram.Update, context: telegram.ext.ContextTypes.DEFAULT_TYPE) -> None:
		user_id = str(update.message.from_user.id)
		await update.message.reply_text(f'You currently have {self.get_balance(user_id)} Aura.')

	async def leaderboard(self, update: telegram.Update, context: telegram.ext.ContextTypes.DEFAULT_TYPE) -> None:
		chat = update.message.chat
		chat_id = chat.id
		leaderboard_dict = {}

		# Populate the leaderboard dictionary with usernames or fallback to first names
		for user_id in self.aura_balances:
			try:
				chat_member = await context.bot.get_chat_member(chat_id=chat_id, user_id=int(user_id))
				username = chat_member.user.username or chat_member.user.first_name  # Use username if available, else first name
				leaderboard_dict[username] = self.aura_balances[user_id]
			except Exception as e:
				# Handle potential errors (e.g., user is no longer in the chat)
				continue

		# Sort the leaderboard dictionary by balance values (descending)
		sorted_leaderboard = sorted(leaderboard_dict.items(), key=lambda item: item[1], reverse=True)

		# Format the leaderboard as a numbered list without using enumerate
		leaderboard_string = ""
		rank = 1
		for username, balance in sorted_leaderboard:
			leaderboard_string += f"{rank}. {username}: {balance}\n"
			rank += 1

		# Add a title or header to the leaderboard message
		if leaderboard_string:
			leaderboard_message = f"{leaderboard_string}"
		else:
			leaderboard_message = "No data available for the leaderboard."

		# Send the leaderboard message
		await update.message.reply_text(
			leaderboard_message,
		)
	
	async def lockin(self, update: telegram.Update, context: telegram.ext.ContextTypes.DEFAULT_TYPE) -> None:
		pass
