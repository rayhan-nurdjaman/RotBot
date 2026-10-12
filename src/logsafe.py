import logging
import re

# A Telegram bot token is '<bot id>:<35 characters>'. httpx logs every request URL, and Telegram puts the token in it
# ('https://api.telegram.org/bot<token>/sendMessage'), so without this the token ends up in app.log (issue #49).
_TELEGRAM_TOKEN = re.compile(r'\d{5,}:[A-Za-z0-9_-]{30,}')

# Redacts the finished text, so the message, its arguments and any traceback are all covered.
class RedactingFormatter(logging.Formatter):
	def format(self, record):
		return _TELEGRAM_TOKEN.sub('<redacted>', super().format(record))
