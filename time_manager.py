from datetime import datetime, timezone, timedelta

class TimeManager:
	def __init__(self):
		self.reminders = {}
		self.zone_offset = 8 # Singapore

	async def poll(self, callback):
		# Get current utc unix time 
		now = datetime.now(timezone.utc)
		current_unix_time = int(now.timestamp())
		# Seperated per chat conversation
		for chat_id in self.reminders:
			for i in range(len(self.reminders[chat_id]) - 1, -1, -1):
				reminder = self.reminders[chat_id][i]
				if reminder['unix_time'] < current_unix_time:
					await callback(chat_id, reminder)
					self.reminders[chat_id].pop(i)

	def get_time(self):
		now = datetime.now(timezone.utc)
		offset_time = now + timedelta(hours=self.zone_offset)

		day_name = offset_time.strftime("%A")
		datetime_with_real_time = datetime.combine(offset_time.date(), offset_time.time())

		return {
			'time' : str(datetime_with_real_time),
			'day' : day_name
		}	

	def add_reminder(self, chat_id, description, year, month, day, hour, minute, second):
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
			return 'Failed creating reminder because of invalid parameters.'
		else:
			utc_offset = timezone(timedelta(hours=self.zone_offset))  # Define UTC+8 timezone
			dt = dt.replace(tzinfo=utc_offset)  # Set the timezone to UTC+8
			dt_utc = dt.astimezone(timezone.utc)
			now_utc = datetime.now(timezone.utc)

			datetime_with_real_time = datetime.combine(dt.date(), dt.time())
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
				'state' : f'Reminder \'{description}\' successfully created at {str(datetime_with_real_time)}'
			}
