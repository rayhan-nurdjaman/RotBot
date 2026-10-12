import logging
from datetime import timedelta

import clock as Clock
import db
from config import config

logger = logging.getLogger(__name__)

# What each LLM call cost, per chat, in the `usage` table (see db.py). One row per call, written by
# chat_completion.create from OpenRouter's usage object. The cost is in USD as OpenRouter reports it ('credits').
# Spend is counted per chat and per calendar day in the configured timezone (config.yaml `daily_cap_usd`).
# The cap is checked once at the start of a turn, so a turn that starts just under it can end over it.

# Raised by check() when the chat has used up its budget for today.
class CapReached(Exception):
	pass

def _now():
	return int(Clock.now().timestamp())

# Unix seconds of local midnight, `days_back` days ago (0 is the start of today).
def _midnight(days_back=0):
	midnight = Clock.now_local().replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days_back)
	return int(midnight.timestamp())

# Stores one call. Never raises: the call has already been paid for and answered, and its numbers are in the log.
# Anything missing from OpenRouter's usage object is stored as NULL. Without a chat id there is nothing to count against.
async def record(chat_id, model, usage):
	if chat_id is None:
		return
	completion_details = getattr(usage, 'completion_tokens_details', None)
	prompt_details = getattr(usage, 'prompt_tokens_details', None)
	try:
		async with db.connection().execute(
			'INSERT INTO usage (chat_id, created_at, model, effort, prompt_tokens, completion_tokens, reasoning_tokens, cached_tokens, cost) '
			'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
			(
				chat_id, _now(), model.slug, model.effort,
				getattr(usage, 'prompt_tokens', None),
				getattr(usage, 'completion_tokens', None),
				getattr(completion_details, 'reasoning_tokens', None),
				getattr(prompt_details, 'cached_tokens', None),
				getattr(usage, 'cost', None),
			),
		):
			pass
		await db.connection().commit()
	except Exception:
		logger.exception(f'Could not record the usage of chat {chat_id}.')

# (cost in USD, number of calls) of this chat since `since` (Unix seconds). Calls with no reported cost count as 0.
async def total(chat_id, since=0):
	async with db.connection().execute(
		'SELECT COALESCE(SUM(cost), 0), COUNT(*) FROM usage WHERE chat_id = ? AND created_at >= ?',
		(chat_id, since),
	) as cursor:
		cost, calls = await cursor.fetchone()
	return cost, calls

# Raises CapReached if the chat has spent its daily budget. If the database cannot be read this logs and lets the
# turn go ahead (the same choice as Instance.load_memory), so a database problem does not silence the bot.
async def check(chat_id):
	try:
		spent, _ = await total(chat_id, _midnight())
	except Exception:
		logger.exception(f'Could not read the usage of chat {chat_id}, not enforcing the daily cap.')
		return
	if spent >= config['daily_cap_usd']:
		raise CapReached(f'Chat {chat_id} has spent ${spent:.4f} today, the cap is ${config["daily_cap_usd"]:.2f}.')

# The text of /usage: only this chat's own numbers.
async def report(chat_id):
	cap = config['daily_cap_usd']
	today, today_calls = await total(chat_id, _midnight())
	week, week_calls = await total(chat_id, _midnight(6))
	everything, all_calls = await total(chat_id)
	return (
		f'Usage of this chat (daily cap ${cap:.2f}, resets at midnight {Clock.zone.key}):\n'
		f'Today: ${today:.4f} in {today_calls} calls, ${max(cap - today, 0):.4f} left\n'
		f'Last 7 days: ${week:.4f} in {week_calls} calls\n'
		f'All time: ${everything:.4f} in {all_calls} calls'
	)
