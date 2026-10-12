import logging
import os

logger = logging.getLogger(__name__)

# Who may use the bot (numeric Telegram user ids), and in which chats the bash tool works (numeric chat ids).
# Both come from the environment (.env) and are read once by load() at startup. Until load() has run, both are
# empty, which denies everybody: the check fails closed.
_allowed_users = frozenset()
_op_chats = frozenset()

class AccessConfigError(ValueError):
	pass

def _parse(name, text, positive_only):
	ids = set()
	for entry in (text or '').split(','):
		entry = entry.strip()
		if not entry:
			continue
		try:
			number = int(entry)
		except ValueError:
			raise AccessConfigError(f'{name} must list numeric Telegram ids separated by commas, but it contains \'{entry}\'. Usernames do not work.')
		if positive_only and number <= 0:
			raise AccessConfigError(f'{name} must list user ids, which are positive numbers, but it contains \'{entry}\'. A negative number is a group chat id.')
		ids.add(number)
	return frozenset(ids)

# Raises AccessConfigError when the allow-list is empty or any entry is not valid, so the bot does not start
# open or half configured. OP_CHAT_IDS may be empty: the bash tool is then off everywhere.
def load(environ=os.environ):
	global _allowed_users, _op_chats
	users = _parse('ALLOWED_USER_IDS', environ.get('ALLOWED_USER_IDS'), positive_only=True)
	if not users:
		raise AccessConfigError('ALLOWED_USER_IDS is empty or missing. Put the numeric Telegram user ids that may use the bot in .env, separated by commas.')
	ops = _parse('OP_CHAT_IDS', environ.get('OP_CHAT_IDS'), positive_only=False)
	_allowed_users, _op_chats = users, ops
	logger.info(f'Access: {len(users)} allowed user(s), bash enabled in {len(ops)} chat(s).')

def is_allowed(user_id):
	return user_id in _allowed_users

def is_op_chat(chat_id):
	return chat_id in _op_chats
