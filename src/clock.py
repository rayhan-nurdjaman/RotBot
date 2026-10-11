from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from config import config

# One timezone for the whole bot (config.yaml `timezone`). Times are stored in UTC and shown in this zone.
zone = ZoneInfo(config['timezone'])

# Tests replace this to control time.
def now():
	return datetime.now(timezone.utc)

def now_local():
	return now().astimezone(zone)

# Prefix of every stored user message, e.g. '2026-10-11 Sun 21:54'. It is part of the message text and never
# changes afterwards, which is what lets the history stay cacheable. The zone is named once in the system prompt.
def stamp(moment=None):
	return f'{moment or now_local():%Y-%m-%d %a %H:%M}'
