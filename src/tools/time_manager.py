from datetime import datetime, timezone, timedelta
from instance import Instance

# Need to put this in database for efficiency, currently here for the time being
reminders = {}
zone_offset = 8 # Singapore

async def poll(on_reminder):
	# Get current utc unix time 
	now = datetime.now(timezone.utc)
	current_unix_time = int(now.timestamp())
	# Seperated per chat conversation
	for instance_id in reminders:
		for i in range(len(reminders[instance_id]) - 1, -1, -1):
			reminder = reminders[instance_id][i]
			if reminder['unix_time'] < current_unix_time:
				await on_reminder(instance_id, reminder)
				reminders[instance_id].pop(i)

def get_time():
	now = datetime.now(timezone.utc)
	offset_time = now + timedelta(hours=zone_offset)

	day_name = offset_time.strftime("%A")
	datetime_with_real_time = datetime.combine(offset_time.date(), offset_time.time())

	return {
		'time' : str(datetime_with_real_time),
		'day' : day_name
	}

def add_reminder(instance_id, description, year, month, day, hour, minute, second):
	try:
		# Sanitize input. Checks if date is actually valid.
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
			'state' : 'Failed to create reminder due to invalid parameters.',
		}
	else:
		utc_offset = timezone(timedelta(hours=zone_offset))  # Define UTC+8 timezone
		dt = dt.replace(tzinfo=utc_offset)  # Set the timezone to UTC+8
		dt_utc = dt.astimezone(timezone.utc)
		now_utc = datetime.now(timezone.utc)

		datetime_with_real_time = datetime.combine(dt.date(), dt.time())
		unix_time = int(dt_utc.timestamp())
		
		if not instance_id in reminders:
			reminders[instance_id] = []
		reminders[instance_id].append(
			{
				'unix_time' : unix_time,
				'description' : description
			}
		)
		return {
			'state' : 'Reminder successfully created',
			'description' : description,
			'when' : str(datetime_with_real_time)
		}
