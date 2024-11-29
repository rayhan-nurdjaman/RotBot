# from logging_config import logger

import telegram
import telegram.ext

import time
import asyncio
import json
import random
import io

import tenor
import assistant
import os

class Client:
	def __init__(self, token, assistant):
		self.application = telegram.ext.Application.builder().token(token).build()
		self.assistant = assistant
		self.register_handlers()
		self.enable_always_on = {}
		self.WPM = 400
		self.function_call_limit = 99
		self.tenor = tenor.Tenor(token=os.getenv('GOOGLE_API_KEY'))
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

		print('Client object initialized.\n')

	def register_handlers(self):
		self.application.add_handler(telegram.ext.CommandHandler('rollhumor', self.roll_for_humor))
		self.application.add_handler(telegram.ext.CommandHandler('penis', self.penis))
		self.application.add_handler(telegram.ext.CommandHandler('stfu', self.toggle_always_on))
		self.application.add_handler(telegram.ext.CommandHandler('coinflip', self.coinflip))
		self.application.add_handler(telegram.ext.CommandHandler('aura', self.aura))
		self.application.add_handler(telegram.ext.CommandHandler('leaderboard', self.leaderboard))
		self.application.add_handler(telegram.ext.MessageHandler(telegram.ext.filters.ALL, self.on_message))

	async def on_message(self, update, context):
		chat_id = str(update.message.chat.id)
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

		if (user_message or photo_url) and self.can_talk(chat_id) and self.assistant.is_user_addressing(chat_id):
			response = await self.assistant.get_response(chat_id)

			n = 1
			# Model tried calling a tool
			while response.finish_reason != 'stop' and n < self.function_call_limit:
				# Properly handle tool calls here
				await self._handle_tool_call(update, context, response)
				# Have the assistant regenerate response after calling a function
				response = await self.assistant.get_response(chat_id)
				n += 1

			self.assistant.add_assistant_message(chat_id, response) # Finish reason == 'stop'
			await self._send_message(update, context, response)

	def run(self):
		self.application.run_polling(allowed_updates=telegram.Update.ALL_TYPES)

	# Helper functions
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

	async def _send_message(self, update, context, response):
		chat_bubbles = response.message.content.splitlines()

		for i in range(len(chat_bubbles)):
			bubble = chat_bubbles[i]
			if bubble and not bubble.isspace():
				await context.bot.send_chat_action(chat_id=update.message.chat_id, action=telegram.constants.ChatAction.TYPING)
				if i == 0:
					await update.message.reply_text(bubble)
				else:
					await asyncio.sleep(min(len(bubble)/5 / (self.WPM/60), 5.0))
					await context.bot.send_message(chat_id=update.message.chat_id, text=bubble)
				await asyncio.sleep(0.2)

	async def _handle_image(self, update, context):
		file = await context.bot.get_file(update.message.photo[-1].file_id)

		out_buffer = io.BytesIO()
		await file.download_to_memory(out_buffer)
		out_buffer.seek(0)
		data = out_buffer.read()

		return await self.assistant.encode_image(data)

	async def _handle_tool_call(self, update, context, response):
		chat_id = str(update.message.chat.id)

		tool_call = response.message.tool_calls[0]
		tool_name = tool_call.function.name
		tool_arguments = json.loads(tool_call.function.arguments)

		tool_output = None

		if tool_name == 'save_to_memory':
			tool_output = self.assistant.add_to_memory(tool_arguments.get('user_info'), chat_id)
			await update.message.set_reaction(telegram.constants.ReactionEmoji.WRITING_HAND)
		elif tool_name == 'send_gif':
			tool_output = await self.send_tenor_gif(update, context, tool_arguments.get('search_term'))

		self.assistant.add_tool_message(chat_id, tool_call, tool_output)

	def dump(self) -> None:
		with open('data/aura_balances.json', 'w') as file:
			file.write(json.dumps(self.aura_balances, indent=4))

	# Client Tools
	async def send_tenor_gif(self, update, context, search_term):
		print(f'RotBot tried searching for {search_term} on Tenor.\n')
		url = self.tenor.random(search_term)
		await context.bot.send_animation(chat_id=update.message.chat_id, animation=url)
		return {
			'search_term' : search_term,
			'state' : 'Sent GIF successfully.'
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
