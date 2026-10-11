import re

import clock as Clock
import db

# Memories live in the `memories` table (see db.py), scoped to a chat: every function takes the chat id and
# never touches another chat's rows. A memory is a short text. It can be a fact ("alice likes tea") or a standing
# instruction for the chat ("reply in a formal tone").
#
# Two ways the model gets at them: the newest ones are put in the prompt every turn (for_prompt), and the rest
# are found with search(). search() is the one place to replace if retrieval ever becomes vector search.

MAX_LENGTH = 300		# characters in one memory
MAX_PER_CHAT = 200
PROMPT_COUNT = 50		# at most this many memories go into the prompt...
PROMPT_CHARS = 4000		# ...and at most this many characters of them, newest kept first
RECALL_LIMIT = 10		# results of one search
LIST_COUNT = 30			# what /memory shows, kept under Telegram's 4096 character limit for one message
LIST_CHARS = 3000

# Raised by add() when a memory must not be saved. The message is written for the model.
class Rejected(Exception):
	pass

# Obvious secrets only. This is a safety net behind the prompt guidance, not a classifier.
_SENSITIVE = [
	re.compile(r'(?:\d[ -]?){13,19}'),																# card number
	re.compile(r'\bsk-[A-Za-z0-9_-]{16,}'),															# API key
	re.compile(r'-----BEGIN [A-Z ]*PRIVATE KEY'),
	re.compile(r'\b(?:password|passcode|passphrase|pin|api[ _-]?key|secret|token)\b[^.\n]{0,20}?(?:\bis\b|=|:)', re.IGNORECASE),
]

def _now():
	return int(Clock.now().timestamp())

# Saves a memory. Returns (id, created); created is False when the chat already has the same text, in which case
# the existing id is returned. Raises Rejected for empty, too long, sensitive-looking text, or a full chat.
async def add(chat_id, text, created_by=None):
	text = ' '.join(text.split())
	if not text:
		raise Rejected('The memory is empty.')
	if len(text) > MAX_LENGTH:
		raise Rejected(f'That memory is {len(text)} characters long and the limit is {MAX_LENGTH}. Save only the essential fact.')
	if any(pattern.search(text) for pattern in _SENSITIVE):
		raise Rejected('That looks like a password, key or card number. Those must never be saved.')

	async with db.connection().execute('SELECT id FROM memories WHERE chat_id = ? AND lower(text) = lower(?)', (chat_id, text)) as cursor:
		existing = await cursor.fetchone()
	if existing:
		return existing['id'], False

	# The limit is checked inside the INSERT, so two saves running at once cannot both pass it.
	async with db.connection().execute(
		'INSERT INTO memories (chat_id, created_by, text, created_at) SELECT ?, ?, ?, ? WHERE (SELECT COUNT(*) FROM memories WHERE chat_id = ?) < ?',
		(chat_id, created_by, text, _now(), chat_id, MAX_PER_CHAT),
	) as cursor:
		memory_id = cursor.lastrowid if cursor.rowcount else None
	await db.connection().commit()
	if memory_id is None:
		raise Rejected(f'This chat already has {MAX_PER_CHAT} memories, which is the limit. Forget some first: recall_memory shows them and forget_memory removes one.')
	return memory_id, True

# The chat's newest `limit` memories, oldest of those first, and how many older ones are not included.
async def recent(chat_id, limit):
	async with db.connection().execute('SELECT id, text FROM memories WHERE chat_id = ? ORDER BY id DESC LIMIT ?', (chat_id, limit)) as cursor:
		entries = [dict(row) for row in reversed(await cursor.fetchall())]
	async with db.connection().execute('SELECT COUNT(*) FROM memories WHERE chat_id = ?', (chat_id,)) as cursor:
		total = (await cursor.fetchone())[0]
	return entries, total - len(entries)

# What goes into the prompt: (entries, hidden), entries in a fixed order (oldest first) so the prompt text only
# changes when a memory is saved or removed, and hidden the number of memories left out by the caps.
# /memory uses it with its own, smaller caps.
async def for_prompt(chat_id, count=PROMPT_COUNT, max_chars=PROMPT_CHARS):
	entries, hidden = await recent(chat_id, count)
	kept, characters = [], 0
	for entry in reversed(entries):
		characters += len(entry['text'])
		if characters > max_chars:
			break
		kept.append(entry)
	kept.reverse()
	return kept, hidden + len(entries) - len(kept)

# Memories of the chat that share words with the query, best match first, then newest first. An empty query
# returns the newest. Matching is by whole query words found anywhere in the text, so "like" finds "likes".
async def search(chat_id, query, limit=RECALL_LIMIT):
	async with db.connection().execute('SELECT id, text FROM memories WHERE chat_id = ? ORDER BY id DESC', (chat_id,)) as cursor:
		rows = [dict(row) for row in await cursor.fetchall()]
	words = set(re.findall(r'\w{2,}', query.lower()))
	if not words:
		return rows[:limit]
	scored = [(sum(word in row['text'].lower() for word in words), row) for row in rows]
	scored = [pair for pair in scored if pair[0] > 0]
	scored.sort(key=lambda pair: -pair[0])
	return [row for _, row in scored[:limit]]

# Returns whether a memory of this chat was deleted. Other chats' memories are never touched.
async def delete(chat_id, memory_id):
	async with db.connection().execute('DELETE FROM memories WHERE id = ? AND chat_id = ?', (memory_id, chat_id)) as cursor:
		deleted = cursor.rowcount > 0
	await db.connection().commit()
	return deleted
