import asyncio
import logging
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import openai
import pytest

import clock as Clock
import db
import main
import reminders as Reminders
from tools.native import registry
from tools.registry import ToolContext

START = datetime(2026, 10, 11, 12, 0, 0, tzinfo=timezone.utc)    # 20:00 in Singapore


class FakeTime:
    def __init__(self):
        self.moment = START

    def advance(self, **kwargs):
        self.moment += timedelta(**kwargs)

    def unix(self, **kwargs):
        return int((self.moment + timedelta(**kwargs)).timestamp())


@pytest.fixture
def fake_time(monkeypatch):
    fake = FakeTime()
    monkeypatch.setattr(Clock, 'now', lambda: fake.moment)
    return fake


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / 'rotbot.db')


def with_db(path):
    """Runs an async test body with an open database, closing it afterwards."""
    def decorator(body):
        async def go():
            await db.init(path)
            try:
                await body()
            finally:
                await db.close()
        asyncio.run(go())
    return decorator


class Recorder:
    def __init__(self, fail_times=0):
        self.delivered = []
        self.fail_times = fail_times

    async def __call__(self, reminder, late_seconds):
        if self.fail_times:
            self.fail_times -= 1
            raise RuntimeError('telegram is down')
        self.delivered.append((reminder['description'], late_seconds))


async def row(reminder_id):
    async with db.connection().execute('SELECT * FROM reminders WHERE id = ?', (reminder_id,)) as cursor:
        return dict(await cursor.fetchone())


# ---- store and scheduler -------------------------------------------------------------------------------------


def test_only_due_reminders_are_delivered_oldest_first(db_path, fake_time):
    @with_db(db_path)
    async def body():
        await Reminders.add(1, 'second', fake_time.unix(minutes=-1))
        await Reminders.add(1, 'first', fake_time.unix(minutes=-5))
        await Reminders.add(1, 'later', fake_time.unix(hours=1))
        deliver = Recorder()

        delay = await Reminders.process_due(deliver)

        assert [d for d, _ in deliver.delivered] == ['first', 'second']
        assert delay == Reminders.MAX_SLEEP
        # Delivered ones are not delivered again.
        await Reminders.process_due(deliver)
        assert len(deliver.delivered) == 2


def test_sleep_is_until_the_next_reminder_but_capped(db_path, fake_time):
    @with_db(db_path)
    async def body():
        await Reminders.add(1, 'soon', fake_time.unix(seconds=20))
        assert await Reminders.process_due(Recorder()) == 20
        await Reminders.add(1, 'sooner', fake_time.unix(seconds=5))
        assert await Reminders.process_due(Recorder()) == 5


def test_reminders_due_while_the_bot_was_down_are_delivered_late(db_path, fake_time):
    @with_db(db_path)
    async def body():
        await Reminders.add(1, 'missed', fake_time.unix(minutes=10))
        fake_time.advance(hours=3)
        deliver = Recorder()

        await Reminders.process_due(deliver)

        assert deliver.delivered == [('missed', 10800 - 600)]


def test_a_reminder_survives_a_restart_and_fires_once(db_path, fake_time):
    async def first_process():
        await db.init(db_path)
        await Reminders.add(1, 'call mom', fake_time.unix(seconds=30))
        await db.close()

    async def second_process():
        await db.init(db_path)
        deliver = Recorder()
        await Reminders.process_due(deliver)
        fake_time.advance(minutes=1)
        await Reminders.process_due(deliver)
        await Reminders.process_due(deliver)
        await db.close()
        return deliver

    asyncio.run(first_process())
    deliver = asyncio.run(second_process())

    assert [d for d, _ in deliver.delivered] == ['call mom']


def test_a_failed_delivery_is_retried_with_backoff_then_succeeds(db_path, fake_time):
    @with_db(db_path)
    async def body():
        reminder_id = await Reminders.add(1, 'x', fake_time.unix(seconds=-1))
        deliver = Recorder(fail_times=2)

        await Reminders.process_due(deliver)
        assert (await row(reminder_id))['status'] == 'pending'
        assert (await row(reminder_id))['attempts'] == 1
        # Not retried before the backoff has passed.
        fake_time.advance(seconds=Reminders.RETRY_DELAYS[0] - 1)
        await Reminders.process_due(deliver)
        assert (await row(reminder_id))['attempts'] == 1

        fake_time.advance(seconds=1)
        await Reminders.process_due(deliver)
        assert (await row(reminder_id))['attempts'] == 2
        fake_time.advance(seconds=Reminders.RETRY_DELAYS[1])
        await Reminders.process_due(deliver)

        assert deliver.delivered != []
        assert (await row(reminder_id))['status'] == 'done'


def test_a_reminder_that_keeps_failing_is_marked_failed_with_a_critical_log(db_path, fake_time, caplog):
    @with_db(db_path)
    async def body():
        reminder_id = await Reminders.add(1, 'x', fake_time.unix(seconds=-1))
        deliver = Recorder(fail_times=100)

        for _ in range(Reminders.MAX_ATTEMPTS):
            await Reminders.process_due(deliver)
            fake_time.advance(hours=1)
        await Reminders.process_due(deliver)

        stored = await row(reminder_id)
        assert stored['status'] == 'failed'
        assert stored['attempts'] == Reminders.MAX_ATTEMPTS
        assert any(r.levelno == logging.CRITICAL for r in caplog.records)


def test_one_failing_reminder_does_not_block_the_others(db_path, fake_time):
    @with_db(db_path)
    async def body():
        await Reminders.add(1, 'bad', fake_time.unix(seconds=-10))
        await Reminders.add(2, 'good', fake_time.unix(seconds=-5))
        delivered = []

        async def deliver(reminder, late):
            if reminder['description'] == 'bad':
                raise RuntimeError('boom')
            delivered.append(reminder['description'])

        await Reminders.process_due(deliver)

        assert delivered == ['good']


def test_a_reminder_cancelled_during_an_earlier_delivery_is_not_delivered(db_path, fake_time):
    @with_db(db_path)
    async def body():
        await Reminders.add(1, 'first', fake_time.unix(seconds=-10))
        second = await Reminders.add(1, 'second', fake_time.unix(seconds=-5))
        delivered = []

        async def deliver(reminder, late):
            delivered.append(reminder['description'])
            await Reminders.cancel(1, second)

        await Reminders.process_due(deliver)

        assert delivered == ['first']


def test_cancel_only_affects_pending_reminders_of_the_same_chat(db_path, fake_time):
    @with_db(db_path)
    async def body():
        mine = await Reminders.add(1, 'mine', fake_time.unix(hours=1))
        theirs = await Reminders.add(2, 'theirs', fake_time.unix(hours=1))

        assert await Reminders.cancel(1, theirs) is False
        assert await Reminders.cancel(1, 999) is False
        assert await Reminders.cancel(1, mine) is True
        assert await Reminders.cancel(1, mine) is False
        assert [r['description'] for r in await Reminders.pending(2)] == ['theirs']
        assert await Reminders.pending(1) == []


def test_the_scheduler_survives_failures_and_is_woken_by_an_earlier_reminder(db_path, fake_time, monkeypatch):
    monkeypatch.setattr(Reminders, 'CRASH_RETRY', 0.01)

    @with_db(db_path)
    async def body():
        delivered = []

        async def deliver(reminder, late):
            delivered.append(reminder['description'])

        real = Reminders.process_due
        calls = {'n': 0}

        async def flaky(deliver):
            calls['n'] += 1
            if calls['n'] == 1:
                raise RuntimeError('database is locked')
            return await real(deliver)

        monkeypatch.setattr(Reminders, 'process_due', flaky)
        task = asyncio.create_task(Reminders.run(deliver))
        await asyncio.sleep(0.1)                     # survived the crash, now sleeping for MAX_SLEEP
        assert not task.done()

        # It is asleep until the clock is MAX_SLEEP later; adding a due reminder must wake it up anyway.
        await Reminders.add(1, 'urgent', fake_time.unix(seconds=-1))
        for _ in range(50):
            if delivered:
                break
            await asyncio.sleep(0.02)

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert delivered == ['urgent']
        assert Reminders._wake is None


# ---- tools ---------------------------------------------------------------------------------------------------


def call(name, args, chat_id='chat-1'):
    return registry.call(name, args, ToolContext(main.Instance(chat_id), None))


def test_set_reminder_reads_a_naive_time_in_the_configured_zone(db_path, fake_time):
    @with_db(db_path)
    async def body():
        result = await call('set_reminder', {'description': 'call mom', 'when': '2026-10-11T21:30:00'})

        assert result['ok'] is True
        assert result['when'] == '2026-10-11T21:30+08:00'
        stored = (await Reminders.pending('chat-1'))[0]
        assert stored['due_at'] == int(datetime(2026, 10, 11, 13, 30, tzinfo=timezone.utc).timestamp())
        assert stored['description'] == 'call mom'


def test_set_reminder_honours_an_explicit_offset(db_path, fake_time):
    @with_db(db_path)
    async def body():
        result = await call('set_reminder', {'description': 'x', 'when': '2026-10-11T09:00:00-04:00'})

        assert result['when'] == '2026-10-11T21:00+08:00'
        assert (await Reminders.pending('chat-1'))[0]['due_at'] == int(datetime(2026, 10, 11, 13, 0, tzinfo=timezone.utc).timestamp())


def test_set_reminder_accepts_a_utc_z_suffix(db_path, fake_time):
    @with_db(db_path)
    async def body():
        result = await call('set_reminder', {'description': 'x', 'when': '2026-10-11T14:00:00Z'})

        assert result['ok'] is True
        assert result['when'] == '2026-10-11T22:00+08:00'


@pytest.mark.parametrize('when', ['tomorrow at 9', '2026-13-01T10:00:00', '2026-10-11T25:00:00', ''])
def test_set_reminder_rejects_text_that_is_not_iso_8601(db_path, fake_time, when):
    @with_db(db_path)
    async def body():
        result = await call('set_reminder', {'description': 'x', 'when': when})

        assert result['ok'] is False
        assert 'ISO 8601' in result['error']
        assert await Reminders.pending('chat-1') == []


@pytest.mark.parametrize('when', ['2026-10-11T19:59:00', '2026-10-11T20:00:00', '2020-01-01T00:00:00'])
def test_set_reminder_rejects_times_that_are_not_in_the_future(db_path, fake_time, when):
    @with_db(db_path)
    async def body():
        result = await call('set_reminder', {'description': 'x', 'when': when})

        assert result['ok'] is False
        assert 'not in the future' in result['error']
        assert '2026-10-11 Sun 20:00' in result['error']
        assert await Reminders.pending('chat-1') == []


def test_a_wall_clock_time_is_converted_with_the_zone_rules_of_that_date(db_path, fake_time, monkeypatch):
    monkeypatch.setattr(Clock, 'zone', Clock.ZoneInfo('America/New_York'))

    @with_db(db_path)
    async def body():
        # After the autumn clock change New York is UTC-5, before it UTC-4.
        await call('set_reminder', {'description': 'summer', 'when': '2026-10-20T12:00:00'})
        await call('set_reminder', {'description': 'winter', 'when': '2026-11-20T12:00:00'})

        by_name = {r['description']: r['due_at'] for r in await Reminders.pending('chat-1')}
        assert by_name['summer'] == int(datetime(2026, 10, 20, 16, 0, tzinfo=timezone.utc).timestamp())
        assert by_name['winter'] == int(datetime(2026, 11, 20, 17, 0, tzinfo=timezone.utc).timestamp())


def test_list_and_cancel_reminder_are_scoped_to_the_chat(db_path, fake_time):
    @with_db(db_path)
    async def body():
        mine = (await call('set_reminder', {'description': 'mine', 'when': '2026-10-12T09:00:00'}))['id']
        theirs = (await call('set_reminder', {'description': 'theirs', 'when': '2026-10-12T10:00:00'}, chat_id='chat-2'))['id']

        listed = await call('list_reminders', {})
        assert listed == {'ok': True, 'reminders': [{'id': mine, 'description': 'mine', 'when': '2026-10-12T09:00+08:00'}]}

        refused = await call('cancel_reminder', {'id': theirs})
        assert refused['ok'] is False
        assert (await call('cancel_reminder', {'id': mine})) == {'ok': True, 'cancelled': mine}
        assert (await call('list_reminders', {}))['reminders'] == []
        assert len(await Reminders.pending('chat-2')) == 1


def test_reminder_tools_have_strict_schemas():
    schemas = {s['function']['name']: s['function'] for s in registry.schemas() if s['type'] == 'function'}

    assert schemas['set_reminder']['parameters']['required'] == ['description', 'when']
    assert schemas['set_reminder']['parameters']['properties']['when']['type'] == 'string'
    assert schemas['list_reminders']['parameters'] == {'type': 'object', 'properties': {}, 'required': [], 'additionalProperties': False}
    assert schemas['cancel_reminder']['parameters']['properties']['id']['type'] == 'integer'
    assert all(s['strict'] is True for s in schemas.values())


# ---- delivery ------------------------------------------------------------------------------------------------


def reminder_row(**overrides):
    return {'id': 7, 'chat_id': 'chat-1', 'description': 'call mom', 'due_at': 0, 'attempts': 0, **overrides}


@pytest.fixture
def delivery(monkeypatch):
    """Fakes the model and Telegram. `sent` records what reached the chat, `notes` the notes given to the turn."""
    sent, notes = [], []
    monkeypatch.setattr(main, 'instances', {})

    async def indicator(instance, indicator):
        pass

    async def send_message(chat_id, message):
        sent.append((chat_id, message))

    monkeypatch.setattr(main.Client, 'send_indicator', indicator)
    monkeypatch.setattr(main.Client, 'send_message', send_message)
    return SimpleNamespace(sent=sent, notes=notes, monkeypatch=monkeypatch)


def fake_turn(delivery, result):
    async def get_response(instance, message=None, raise_errors=False):
        delivery.notes.append((message, raise_errors))
        if isinstance(result, Exception):
            raise result
        return result

    delivery.monkeypatch.setattr(main.Assistant, 'get_response', get_response)


def test_delivery_runs_a_turn_and_sends_the_reply(delivery):
    fake_turn(delivery, 'Hey @alice, call mom!')

    asyncio.run(main.on_reminder(reminder_row()))

    assert delivery.sent == [('chat-1', 'Hey @alice, call mom!')]
    message, raise_errors = delivery.notes[0]
    assert 'call mom' in message and 'late' not in message
    assert raise_errors is True


def test_a_late_reminder_tells_the_model_how_late(delivery):
    fake_turn(delivery, 'sorry, late')

    asyncio.run(main.on_reminder(reminder_row(), late_seconds=3 * 3600 + 120))

    assert '3 h 2 min ago' in delivery.notes[0][0]


def test_a_reminder_a_few_seconds_late_is_not_called_late(delivery):
    fake_turn(delivery, 'ok')

    asyncio.run(main.on_reminder(reminder_row(), late_seconds=Reminders.MAX_SLEEP))

    assert 'late' not in delivery.notes[0][0]


def test_when_the_model_is_down_the_reminder_is_sent_as_plain_text(delivery):
    request = SimpleNamespace(method='POST', url='http://x')
    fake_turn(delivery, openai.APIConnectionError(request=request))

    asyncio.run(main.on_reminder(reminder_row()))

    assert delivery.sent == [('chat-1', 'Reminder: call mom')]


def test_when_telegram_is_down_delivery_raises_so_it_is_retried(delivery):
    fake_turn(delivery, 'hi')

    async def send_message(chat_id, message):
        raise RuntimeError('telegram is down')

    delivery.monkeypatch.setattr(main.Client, 'send_message', send_message)

    with pytest.raises(RuntimeError):
        asyncio.run(main.on_reminder(reminder_row()))


def test_delivery_stores_the_message_only_after_the_chat_lock_is_taken(delivery, monkeypatch):
    """The reminder half of issue #32: an 'API:' message must not land in the middle of another turn."""
    from types import SimpleNamespace as NS
    seen = []

    async def create(messages, model, tools=None, chat_id=None, tool_choice=None):
        seen.append([m['role'] for m in messages if m['role'] != 'system'])
        await asyncio.sleep(0.05)
        return NS(finish_reason='stop', message=NS(content='ok', tool_calls=None))

    monkeypatch.setattr(main.Assistant.ChatCompletion, 'create', create)

    async def go():
        user_turn = asyncio.create_task(main.on_message('chat-1', 'alice: hi'))
        await asyncio.sleep(0.01)                    # the user's turn is now inside its LLM call
        await main.on_reminder(reminder_row())
        await user_turn

    asyncio.run(go())

    # The first request holds only the user's message. The reminder message is not part of it.
    assert seen[0] == ['user']
    assert seen[1] == ['user', 'assistant', 'user']


def test_a_user_message_during_a_reminder_turn_never_makes_a_request_end_with_an_assistant_message(delivery, monkeypatch):
    """The user-message half of issue #32: the reminder turn is in flight when a message arrives."""
    from types import SimpleNamespace as NS
    seen = []

    async def create(messages, model, tools=None, chat_id=None, tool_choice=None):
        seen.append([m['role'] for m in messages if m['role'] != 'system'])
        await asyncio.sleep(0.05)
        return NS(finish_reason='stop', message=NS(content='ok', tool_calls=None))

    monkeypatch.setattr(main.Assistant.ChatCompletion, 'create', create)

    async def go():
        reminder = asyncio.create_task(main.on_reminder(reminder_row()))
        await asyncio.sleep(0.01)                    # the reminder turn is now inside its LLM call
        await main.on_message('chat-1', 'alice: hi')
        await reminder

    asyncio.run(go())

    assert seen == [['user'], ['user', 'assistant', 'user']]
    assert all(roles[-1] == 'user' for roles in seen)
