class ReminderManager:
	def __init__(self):
		self.reminders = {}

	async def _handle_reminders(self, callback):
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
					await self._respond(chat_id)
	
	def add_reminder(self):
		if not chat_id in self.reminders:
			self.reminders[chat_id] = []
		self.reminders[chat_id].append(
			{
				'unix_time' : unix_time,
				'description' : description
			}
		)