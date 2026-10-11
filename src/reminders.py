import asyncio
import contextlib
import logging

import clock as Clock
import db

logger = logging.getLogger(__name__)

# Reminders live in the `reminders` table (see db.py), which is the only source of truth. The scheduler below
# sleeps until the next one is due and hands it to a `deliver` callback. Delivery is at-least-once: a reminder is
# marked done only after `deliver` returns, so a crash mid-delivery means it is delivered again after restart.
# Statuses: pending (waiting or being retried), done, failed (gave up after MAX_ATTEMPTS), cancelled.

MAX_ATTEMPTS = 5
# Seconds to wait after the 1st, 2nd, ... failed delivery.
RETRY_DELAYS = [30, 120, 600, 1800]
# The longest the scheduler sleeps in one go. It then looks at the wall clock again, so a clock that jumped
# (NTP, suspend/resume) costs at most this much lateness.
MAX_SLEEP = 60
# How long to wait before trying again when the scheduler itself failed (e.g. the database was unavailable).
CRASH_RETRY = 30

# Set while run() is going. add() sets it so a reminder that is due before the one being waited for is noticed.
# Created inside run() because an Event must not outlive the event loop it was first used in.
_wake = None

def _now():
	return int(Clock.now().timestamp())

def notify():
	if _wake is not None:
		_wake.set()

# due_at: Unix seconds (UTC). Returns the id of the new reminder.
async def add(chat_id, description, due_at, created_by=None):
	async with db.connection().execute(
		'INSERT INTO reminders (chat_id, created_by, description, due_at, attempt_at, created_at) VALUES (?, ?, ?, ?, ?, ?)',
		(chat_id, created_by, description, due_at, due_at, _now()),
	) as cursor:
		reminder_id = cursor.lastrowid
	await db.connection().commit()
	notify()
	return reminder_id

# The chat's reminders that are still waiting, soonest first, as dicts with id, description and due_at.
async def pending(chat_id):
	async with db.connection().execute(
		"SELECT id, description, due_at FROM reminders WHERE chat_id = ? AND status = 'pending' ORDER BY due_at, id",
		(chat_id,),
	) as cursor:
		return [dict(row) for row in await cursor.fetchall()]

# Returns whether a pending reminder of this chat was cancelled. Other chats' reminders are never touched.
async def cancel(chat_id, reminder_id):
	async with db.connection().execute(
		"UPDATE reminders SET status = 'cancelled' WHERE id = ? AND chat_id = ? AND status = 'pending'",
		(reminder_id, chat_id),
	) as cursor:
		cancelled = cursor.rowcount > 0
	await db.connection().commit()
	return cancelled

async def _status(reminder_id):
	async with db.connection().execute('SELECT status FROM reminders WHERE id = ?', (reminder_id,)) as cursor:
		row = await cursor.fetchone()
	return row['status'] if row else None

async def _update(reminder_id, **columns):
	assignments = ', '.join(f'{name} = ?' for name in columns)
	await db.connection().execute(f'UPDATE reminders SET {assignments} WHERE id = ?', (*columns.values(), reminder_id))
	await db.connection().commit()

async def _deliver_one(reminder, deliver):
	# Delivery of an earlier reminder can take a while, and the user may have cancelled this one meanwhile.
	if await _status(reminder['id']) != 'pending':
		return
	late = _now() - reminder['due_at']
	try:
		await deliver(reminder, late)
	except Exception:
		attempts = reminder['attempts'] + 1
		if attempts >= MAX_ATTEMPTS:
			logger.critical(f'Reminder {reminder["id"]} for chat {reminder["chat_id"]} could not be delivered after {attempts} attempts, giving up: {reminder["description"]!r}', exc_info=True)
			await _update(reminder['id'], status='failed', attempts=attempts)
		else:
			retry_in = RETRY_DELAYS[attempts - 1]
			logger.error(f'Delivering reminder {reminder["id"]} failed (attempt {attempts} of {MAX_ATTEMPTS}), retrying in {retry_in}s.', exc_info=True)
			await _update(reminder['id'], attempts=attempts, attempt_at=_now() + retry_in)
	else:
		await _update(reminder['id'], status='done', attempts=reminder['attempts'] + 1)

# Delivers every reminder that is due, one at a time, oldest first. Returns how many seconds the scheduler may
# sleep before the next one is due. `deliver(reminder, late_seconds)` gets the row as a dict and must raise if the
# reminder did not reach the chat. This is also the whole of what the scheduler does per wake-up, kept separate
# so tests can drive it with a fake clock.
async def process_due(deliver):
	async with db.connection().execute(
		"SELECT * FROM reminders WHERE status = 'pending' AND attempt_at <= ? ORDER BY attempt_at, id",
		(_now(),),
	) as cursor:
		due = [dict(row) for row in await cursor.fetchall()]
	for reminder in due:
		await _deliver_one(reminder, deliver)

	async with db.connection().execute("SELECT MIN(attempt_at) FROM reminders WHERE status = 'pending'") as cursor:
		next_at = (await cursor.fetchone())[0]
	if next_at is None:
		return MAX_SLEEP
	return min(MAX_SLEEP, max(0, next_at - _now()))

# The scheduler. Run it as a task for the whole life of the program. Nothing that goes wrong inside it ends it,
# only cancellation does. Reminders already due at startup (the bot was down) are delivered on the first pass.
async def run(deliver):
	global _wake
	_wake = asyncio.Event()
	try:
		while True:
			# Cleared before looking at the table: a reminder added after that look sets the event again,
			# so the wait below returns at once instead of sleeping through it.
			_wake.clear()
			try:
				delay = await process_due(deliver)
			except Exception:
				logger.exception(f'The reminder scheduler failed, trying again in {CRASH_RETRY}s.')
				delay = CRASH_RETRY
			with contextlib.suppress(asyncio.TimeoutError):
				await asyncio.wait_for(_wake.wait(), delay)
	finally:
		_wake = None
