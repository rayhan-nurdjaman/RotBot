import asyncio
from datetime import datetime, timedelta, timezone

import main
import tool_manager as ToolManager
from tools import time_manager as TimeManager


def local_args(dt, description='call mom'):
    return {
        'description': description,
        'year': dt.year, 'month': dt.month, 'day': dt.day,
        'hour': dt.hour, 'minute': dt.minute, 'second': dt.second,
    }


def zone_now():
    return datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=TimeManager.zone_offset))).replace(tzinfo=None)


def test_set_reminder_tool_stores_it_and_returns_a_dict(monkeypatch):
    monkeypatch.setattr(TimeManager, 'reminders', {})

    async def indicator(instance, indicator):
        pass

    monkeypatch.setattr(ToolManager.Client, 'send_indicator', indicator)
    instance = main.Instance('chat-1')

    result = asyncio.run(ToolManager.get_tool_response(
        instance, 'set_reminder', local_args(zone_now() + timedelta(hours=1))))

    assert result['state'] == 'Reminder successfully created'
    assert len(TimeManager.reminders['chat-1']) == 1


def test_due_reminder_fires_once_and_future_one_does_not(monkeypatch):
    monkeypatch.setattr(TimeManager, 'reminders', {})
    now = zone_now()
    TimeManager.add_reminder('chat-1', 'due', *(now - timedelta(minutes=1)).timetuple()[:6])
    TimeManager.add_reminder('chat-1', 'later', *(now + timedelta(hours=1)).timetuple()[:6])
    fired = []

    async def on_reminder(instance_id, reminder):
        fired.append((instance_id, reminder['description']))

    asyncio.run(TimeManager.poll(on_reminder))
    asyncio.run(TimeManager.poll(on_reminder))

    assert fired == [('chat-1', 'due')]
    assert [r['description'] for r in TimeManager.reminders['chat-1']] == ['later']


def test_main_on_reminder_asks_the_model_and_sends_the_reply(monkeypatch):
    monkeypatch.setattr(main, 'instances', {})
    sent = []

    async def indicator(instance, indicator):
        pass

    async def get_response(instance):
        sent.append(('prompt', instance.context_window[-1]['content'][0]['text']))
        return 'Reminder: call mom'

    async def send_message(chat_id, message):
        sent.append((chat_id, message))

    monkeypatch.setattr(main.Client, 'send_indicator', indicator)
    monkeypatch.setattr(main.Client, 'send_message', send_message)
    monkeypatch.setattr(main.Assistant, 'get_response', get_response)

    asyncio.run(main.on_reminder('chat-1', {'description': 'call mom', 'unix_time': 0}))

    assert 'call mom' in sent[0][1]
    assert sent[1] == ('chat-1', 'Reminder: call mom')
