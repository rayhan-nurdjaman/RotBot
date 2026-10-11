import os
import logging
import aiosqlite

logger = logging.getLogger(__name__)

db_path = 'data/rotbot.db'

# Schema changes are appended here, never edited: entry N upgrades a database from version N to N+1.
# The applied version is stored in SQLite's `user_version` pragma. Each migration is one script and runs in
# a transaction together with the version bump, so a failed migration leaves the database unchanged.
MIGRATIONS = [
	# 1: reminders. due_at is the time the user asked for; attempt_at is when delivery is next tried
	# (equal to due_at at first, pushed back after a failed delivery). Both are Unix seconds in UTC.
	'''
	CREATE TABLE reminders (
		id			INTEGER PRIMARY KEY,
		chat_id		INTEGER NOT NULL,
		created_by	TEXT,
		description	TEXT NOT NULL,
		due_at		INTEGER NOT NULL,
		attempt_at	INTEGER NOT NULL,
		attempts	INTEGER NOT NULL DEFAULT 0,
		status		TEXT NOT NULL DEFAULT 'pending',
		created_at	INTEGER NOT NULL
	);
	CREATE INDEX reminders_pending ON reminders (status, attempt_at);
	''',
]

_connection = None

# Opens the database (creating the file and folder if needed) and brings it up to date. Call once at startup.
async def init(path=None):
	global _connection
	path = path or db_path
	directory = os.path.dirname(path)
	if directory:
		os.makedirs(directory, exist_ok=True)
	_connection = await aiosqlite.connect(path)
	_connection.row_factory = aiosqlite.Row
	try:
		await _migrate(_connection)
	except BaseException:
		await close()
		raise

async def close():
	global _connection
	if _connection is not None:
		await _connection.close()
		_connection = None

# The open connection. One connection is shared by the whole program: aiosqlite runs it on its own thread and
# queues calls, so awaiting it never blocks the event loop.
def connection():
	if _connection is None:
		raise RuntimeError('The database is not open. Call db.init() first.')
	return _connection

async def _migrate(conn):
	async with conn.execute('PRAGMA user_version') as cursor:
		version = (await cursor.fetchone())[0]
	if version > len(MIGRATIONS):
		raise RuntimeError(f'The database is at version {version} but this program only knows {len(MIGRATIONS)}. It was written by a newer version.')
	for number in range(version, len(MIGRATIONS)):
		logger.info(f'Migrating the database to version {number + 1}.')
		# executescript commits first and runs outside a transaction, so wrap the script explicitly.
		await conn.executescript(f'BEGIN;\n{MIGRATIONS[number]}\nPRAGMA user_version = {number + 1};\nCOMMIT;')
