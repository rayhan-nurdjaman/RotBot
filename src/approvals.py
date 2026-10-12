import asyncio
import logging
import secrets
from datetime import timedelta

import clock as Clock
from config import config

logger = logging.getLogger(__name__)

# Approval for tool calls that can run code or change things (issue #43, docs/agent-parity.md section 5a).
# While the user is being asked, the model's step is parked here, in memory only: a restart forgets it.
#
# A request is answered exactly once. take() is the only way to get one out, and it removes it, so a button press
# and the expiry timer can never both act on the same request.
#
# 'Approve for the day' approves every call in that chat until local midnight. That is the owner's decision for
# now and a known security weakness, tracked in issue #56.

# The three buttons. Their codes go into the callback data, which Telegram limits to 64 bytes.
ONCE, DAY, DENY = 'once', 'day', 'deny'
ACTIONS = (ONCE, DAY, DENY)
PREFIX = 'ap'

# Display only. What runs is the stored call, never this text.
MAX_MESSAGE_CHARS = 3000
MIN_CHARS_PER_CALL = 120

class Pending:
	def __init__(self, id, chat_id, payload):
		self.id = id
		self.chat_id = chat_id
		self.payload = payload		# whatever the creator needs to resume (assistant.Suspended)
		self.message_id = None		# of the message with the buttons, set once it is sent
		self.notified = False		# True once the chat was told that messages wait for this answer
		self.timer = None

_pending = {}
_day_approved = {}	# chat id -> unix seconds when it ends

def callback_data(id, action):
	return f'{PREFIX}:{id}:{action}'

# Returns (id, action) or None for callback data that is not ours.
def parse_callback_data(data):
	parts = (data or '').split(':')
	if len(parts) != 3 or parts[0] != PREFIX or parts[2] not in ACTIONS:
		return None
	return parts[1], parts[2]

# Stores a request and starts its expiry timer. When the time runs out and nobody answered, `on_expire(pending)` is
# awaited, once. Must be called from a running event loop.
def create(chat_id, payload, on_expire):
	pending = Pending(secrets.token_urlsafe(6), chat_id, payload)
	_pending[pending.id] = pending

	async def expire():
		await asyncio.sleep(config['approval_timeout_s'])
		expired = take(pending.id)
		if expired is None:
			return
		try:
			await on_expire(expired)
		except Exception:
			logger.exception(f'Handling the expiry of approval request {pending.id} failed.')

	pending.timer = asyncio.create_task(expire())
	return pending

# Removes and returns the request, or None if it was already answered, expired, or never existed.
def take(id):
	pending = _pending.pop(id, None)
	if pending is not None and pending.timer is not asyncio.current_task():
		pending.timer.cancel()
	return pending

def peek(id):
	return _pending.get(id)

def count():
	return len(_pending)

# The requests of this chat that are still waiting for an answer, oldest first.
def waiting(chat_id):
	return [pending for pending in _pending.values() if pending.chat_id == chat_id]

# Used at shutdown, so no timer outlives the program's event loop.
def cancel_all():
	for pending in list(_pending.values()):
		take(pending.id)

# 'Approve for the day': until the next local midnight in the configured timezone.
def approve_day(chat_id):
	now = Clock.now_local()
	midnight = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
	_day_approved[chat_id] = int(midnight.timestamp())
	return midnight

def day_approved(chat_id):
	ends = _day_approved.get(chat_id)
	if ends is None:
		return False
	if Clock.now().timestamp() >= ends:
		del _day_approved[chat_id]
		return False
	return True

def _cut(text, limit):
	return text if len(text) <= limit else text[:limit].rstrip() + f' ... [cut for display, {len(text) - limit} more characters will run]'

# The text of the approval message: every call of the step, so the user sees all that will run. Plain text.
# calls: list of (tool name, arguments dict). A bash call is shown as a shell line, anything else as name(arguments).
def describe(calls):
	per_call = max(MIN_CHARS_PER_CALL, MAX_MESSAGE_CHARS // max(1, len(calls)))
	lines = []
	for name, arguments in calls:
		if name == 'bash':
			where = f' (in {arguments["cwd"]})' if arguments.get('cwd') else ''
			lines.append(_cut(f'$ {arguments.get("command", "")}', per_call) + where)
		else:
			shown = ', '.join(f'{key}={value!r}' for key, value in arguments.items())
			lines.append(_cut(f'{name}({shown})', per_call))
	header = 'The assistant wants to run this command:' if len(calls) == 1 else f'The assistant wants to run these {len(calls)} calls together:'
	return header + '\n\n' + '\n\n'.join(lines)
