import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

import assistant as Assistant
import chat_completion as ChatCompletion
import clock as Clock
import db
import main
import usage as Usage
from instance import Instance
from models import parse_model

MODEL = parse_model('haiku-5.5-low')


def usage_object(cost=0.25, prompt=100):
    return SimpleNamespace(
        prompt_tokens=prompt,
        completion_tokens=10,
        completion_tokens_details=SimpleNamespace(reasoning_tokens=4),
        prompt_tokens_details=SimpleNamespace(cached_tokens=60),
        cost=cost,
    )


def at(monkeypatch, year, month, day, hour, minute=0):
    """Sets the clock to a wall-clock time in the configured zone."""
    moment = datetime(year, month, day, hour, minute, tzinfo=Clock.zone).astimezone(timezone.utc)
    monkeypatch.setattr(Clock, 'now', lambda: moment)


@pytest.fixture
def database(tmp_path, monkeypatch):
    """Opens a real SQLite file for the test, with the cap at 1.00 dollar."""
    monkeypatch.setitem(Usage.config, 'daily_cap_usd', 1.0)
    asyncio.run(db.init(str(tmp_path / 'rotbot.db')))
    yield
    asyncio.run(db.close())


def run(coro):
    return asyncio.run(coro)


# These tests run each coroutine in its own event loop, which is fine for aiosqlite.

def test_record_stores_every_field(database, monkeypatch):
    at(monkeypatch, 2026, 10, 12, 12)

    run(Usage.record(7, MODEL, usage_object()))

    async def row():
        async with db.connection().execute('SELECT * FROM usage') as cursor:
            return dict(await cursor.fetchone())

    stored = run(row())
    assert stored['chat_id'] == 7
    assert stored['model'] == 'anthropic/claude-haiku-5.5'
    assert stored['effort'] == 'low'
    assert (stored['prompt_tokens'], stored['completion_tokens'], stored['reasoning_tokens'], stored['cached_tokens']) == (100, 10, 4, 60)
    assert stored['cost'] == 0.25
    assert stored['created_at'] == int(Clock.now().timestamp())


def test_record_keeps_a_call_with_no_usage_numbers_as_nulls(database):
    run(Usage.record(7, MODEL, None))

    assert run(Usage.total(7)) == (0, 1)


def test_record_without_a_chat_id_stores_nothing(database):
    run(Usage.record(None, MODEL, usage_object()))

    assert run(Usage.total(None)) == (0, 0)


def test_record_never_raises_when_the_database_fails(monkeypatch, caplog):
    # No database is open here.
    run(Usage.record(7, MODEL, usage_object()))

    assert 'Could not record the usage of chat 7' in caplog.text


def test_totals_are_per_chat(database):
    run(Usage.record(1, MODEL, usage_object(0.25)))
    run(Usage.record(1, MODEL, usage_object(0.5)))
    run(Usage.record(2, MODEL, usage_object(0.125)))

    assert run(Usage.total(1)) == (0.75, 2)
    assert run(Usage.total(2)) == (0.125, 1)
    assert run(Usage.total(3)) == (0, 0)


def test_today_starts_at_local_midnight(database, monkeypatch):
    at(monkeypatch, 2026, 10, 11, 23, 59)
    run(Usage.record(1, MODEL, usage_object(0.5)))
    at(monkeypatch, 2026, 10, 12, 0, 1)
    run(Usage.record(1, MODEL, usage_object(0.25)))

    at(monkeypatch, 2026, 10, 12, 9)
    assert run(Usage.total(1, Usage._midnight())) == (0.25, 1)
    assert run(Usage.total(1)) == (0.75, 2)


def test_the_cap_blocks_at_or_over_the_limit_and_not_below(database, monkeypatch):
    at(monkeypatch, 2026, 10, 12, 9)
    run(Usage.record(1, MODEL, usage_object(0.9)))
    run(Usage.check(1))                                  # under the cap: fine

    run(Usage.record(1, MODEL, usage_object(0.1)))       # now exactly 1.00
    with pytest.raises(Usage.CapReached):
        run(Usage.check(1))


def test_the_cap_is_per_chat(database, monkeypatch):
    at(monkeypatch, 2026, 10, 12, 9)
    run(Usage.record(1, MODEL, usage_object(5.0)))

    run(Usage.check(2))
    with pytest.raises(Usage.CapReached):
        run(Usage.check(1))


def test_the_cap_resets_at_midnight(database, monkeypatch):
    at(monkeypatch, 2026, 10, 12, 23, 30)
    run(Usage.record(1, MODEL, usage_object(5.0)))
    with pytest.raises(Usage.CapReached):
        run(Usage.check(1))

    at(monkeypatch, 2026, 10, 13, 0, 5)
    run(Usage.check(1))


def test_a_database_failure_does_not_block_the_turn(caplog):
    run(Usage.check(1))

    assert 'not enforcing the daily cap' in caplog.text


def test_report_shows_only_the_chats_own_numbers(database, monkeypatch):
    at(monkeypatch, 2026, 10, 5, 9)
    run(Usage.record(1, MODEL, usage_object(2.0)))        # older than 7 days at the time of the report
    at(monkeypatch, 2026, 10, 10, 9)
    run(Usage.record(1, MODEL, usage_object(0.5)))
    at(monkeypatch, 2026, 10, 12, 9)
    run(Usage.record(1, MODEL, usage_object(0.25)))
    run(Usage.record(2, MODEL, usage_object(9.0)))        # another chat

    text = run(Usage.report(1))

    assert 'Today: $0.2500 in 1 calls, $0.7500 left' in text
    assert 'Last 7 days: $0.7500 in 2 calls' in text
    assert 'All time: $2.7500 in 3 calls' in text
    assert '9.0' not in text


def test_create_records_each_call_with_the_chat_id(database, monkeypatch):
    choice = SimpleNamespace(finish_reason='stop', message=SimpleNamespace(content='hi', tool_calls=None))

    async def create(**kwargs):
        return SimpleNamespace(choices=[choice], usage=usage_object(0.3))

    monkeypatch.setattr(ChatCompletion, 'openai_client', SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))))

    run(ChatCompletion.create([{'role': 'user', 'content': 'hi'}], MODEL, chat_id=42))

    assert run(Usage.total(42)) == (0.3, 1)


def fake_llm(monkeypatch):
    calls = []

    async def create(messages, model, tools=None, chat_id=None, tool_choice=None):
        calls.append(chat_id)
        return SimpleNamespace(finish_reason='stop', message=SimpleNamespace(content='ok', tool_calls=None))

    monkeypatch.setattr(Assistant.ChatCompletion, 'create', create)
    return calls


def test_over_the_cap_a_turn_returns_the_notice_and_makes_no_model_call(database, monkeypatch):
    at(monkeypatch, 2026, 10, 12, 9)
    calls = fake_llm(monkeypatch)
    run(Usage.record('chat-1', MODEL, usage_object(5.0)))
    instance = Instance('chat-1')
    instance.add_user_message('alice: hi')

    assert run(Assistant.get_response(instance)) == Assistant.CAP_MESSAGE
    assert calls == []
    assert instance.context_window[-1]['role'] == 'user'    # nothing was stored for the notice


def test_under_the_cap_the_turn_runs(database, monkeypatch):
    at(monkeypatch, 2026, 10, 12, 9)
    calls = fake_llm(monkeypatch)
    instance = Instance('chat-1')
    instance.add_user_message('alice: hi')

    assert run(Assistant.get_response(instance)) == 'ok'
    assert calls == ['chat-1']


def test_over_the_cap_a_reminder_is_still_sent_as_plain_text(database, monkeypatch):
    at(monkeypatch, 2026, 10, 12, 9)
    calls = fake_llm(monkeypatch)
    run(Usage.record('chat-1', MODEL, usage_object(5.0)))
    monkeypatch.setattr(main, 'instances', {})
    sent = []

    async def indicator(instance, indicator):
        pass

    async def send_message(chat_id, message):
        sent.append((chat_id, message))

    monkeypatch.setattr(main.Client, 'send_indicator', indicator)
    monkeypatch.setattr(main.Client, 'send_message', send_message)

    run(main.on_reminder({'id': 1, 'chat_id': 'chat-1', 'description': 'call mom'}))

    assert calls == []
    assert sent == [('chat-1', 'Reminder: call mom')]


def test_usage_command_sends_only_the_chats_own_report(database, monkeypatch):
    at(monkeypatch, 2026, 10, 12, 9)
    run(Usage.record(1, MODEL, usage_object(0.25)))
    run(Usage.record(2, MODEL, usage_object(7.0)))
    sent = []

    async def send_message(chat_id, message):
        sent.append((chat_id, message))

    monkeypatch.setattr(main.Client, 'send_message', send_message)

    run(main.show_usage(1))

    assert sent[0][0] == 1
    assert '$0.2500' in sent[0][1] and '7.0' not in sent[0][1]


def test_the_usage_table_exists_after_migration(tmp_path):
    path = str(tmp_path / 'rotbot.db')

    async def go():
        await db.init(path)
        async with db.connection().execute("SELECT name FROM sqlite_master WHERE type = 'table'") as cursor:
            names = {row[0] for row in await cursor.fetchall()}
        await db.close()
        return names

    assert 'usage' in run(go())
