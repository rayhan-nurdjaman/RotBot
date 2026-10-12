import asyncio
from datetime import datetime, timezone

import pytest

import approvals
import clock as Clock


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    monkeypatch.setattr(approvals, '_pending', {})
    monkeypatch.setattr(approvals, '_day_approved', {})
    monkeypatch.setitem(approvals.config, 'approval_timeout_s', 60)


def at(monkeypatch, year, month, day, hour, minute=0):
    """Fakes the clock at a local (Asia/Singapore) time."""
    moment = datetime(year, month, day, hour, minute, tzinfo=Clock.zone).astimezone(timezone.utc)
    monkeypatch.setattr(Clock, 'now', lambda: moment)


async def never(pending):
    raise AssertionError('on_expire must not run')


# ---- requests

def test_a_request_can_be_taken_once():
    async def run():
        pending = approvals.create(5, {'step': 1}, never)
        first = approvals.take(pending.id)
        second = approvals.take(pending.id)
        return pending, first, second

    pending, first, second = asyncio.run(run())

    assert first is pending and first.payload == {'step': 1} and first.chat_id == 5
    assert second is None


def test_an_unknown_id_gives_nothing():
    assert approvals.take('nope') is None
    assert approvals.peek('nope') is None


def test_ids_are_distinct_and_unguessable_looking():
    async def run():
        ids = {approvals.create(5, None, never).id for _ in range(50)}
        approvals.cancel_all()
        return ids

    ids = asyncio.run(run())

    assert len(ids) == 50
    assert all(len(i) >= 8 for i in ids)


def test_callback_data_fits_telegrams_limit_and_round_trips():
    async def run():
        pending = approvals.create(-1001234567890, None, never)
        approvals.cancel_all()
        return pending.id

    id = asyncio.run(run())

    for action in approvals.ACTIONS:
        data = approvals.callback_data(id, action)
        assert len(data.encode()) <= 64
        assert approvals.parse_callback_data(data) == (id, action)


@pytest.mark.parametrize('data', [None, '', 'ap', 'ap:x', 'ap:x:maybe', 'zz:x:once', 'ap:x:once:extra'])
def test_other_callback_data_is_not_ours(data):
    assert approvals.parse_callback_data(data) is None


# ---- expiry

def test_an_unanswered_request_expires_once_and_is_gone(monkeypatch):
    monkeypatch.setitem(approvals.config, 'approval_timeout_s', 0.05)
    expired = []

    async def on_expire(pending):
        expired.append(pending.id)

    async def run():
        pending = approvals.create(5, 'payload', on_expire)
        await asyncio.sleep(0.2)
        return pending, approvals.take(pending.id)

    pending, late = asyncio.run(run())

    assert expired == [pending.id]
    assert late is None
    assert approvals.count() == 0


def test_an_answer_before_the_time_cancels_the_timer(monkeypatch):
    monkeypatch.setitem(approvals.config, 'approval_timeout_s', 0.05)

    async def run():
        pending = approvals.create(5, 'payload', never)
        taken = approvals.take(pending.id)
        await asyncio.sleep(0.2)
        return taken, pending.timer

    taken, timer = asyncio.run(run())

    assert taken is not None
    assert timer.cancelled()


def test_a_failing_expiry_handler_is_logged_and_does_not_stop_anything(monkeypatch, caplog):
    monkeypatch.setitem(approvals.config, 'approval_timeout_s', 0.01)

    async def on_expire(pending):
        raise RuntimeError('boom')

    async def run():
        approvals.create(5, None, on_expire)
        await asyncio.sleep(0.1)

    asyncio.run(run())

    assert 'Handling the expiry' in caplog.text
    assert approvals.count() == 0


def test_cancel_all_removes_every_request_and_its_timer():
    async def run():
        pendings = [approvals.create(n, None, never) for n in range(3)]
        approvals.cancel_all()
        await asyncio.sleep(0)
        return pendings

    pendings = asyncio.run(run())

    assert approvals.count() == 0
    assert all(p.timer.cancelled() or p.timer.done() for p in pendings)


# ---- approve for the day

def test_a_day_approval_lasts_until_local_midnight(monkeypatch):
    at(monkeypatch, 2026, 10, 12, 15, 30)
    midnight = approvals.approve_day(5)

    assert (midnight.year, midnight.month, midnight.day, midnight.hour, midnight.minute) == (2026, 10, 13, 0, 0)
    assert approvals.day_approved(5) is True

    at(monkeypatch, 2026, 10, 12, 23, 59)
    assert approvals.day_approved(5) is True

    at(monkeypatch, 2026, 10, 13, 0, 0)
    assert approvals.day_approved(5) is False
    assert approvals.day_approved(5) is False


def test_a_day_approval_does_not_cross_to_another_chat(monkeypatch):
    at(monkeypatch, 2026, 10, 12, 9)
    approvals.approve_day(5)

    assert approvals.day_approved(5) is True
    assert approvals.day_approved(6) is False


def test_approving_a_minute_before_midnight_ends_a_minute_later(monkeypatch):
    at(monkeypatch, 2026, 10, 12, 23, 59)
    approvals.approve_day(5)

    at(monkeypatch, 2026, 10, 13, 0, 0)
    assert approvals.day_approved(5) is False


# ---- what the user sees

def test_a_bash_call_is_shown_as_a_shell_line_with_its_folder():
    text = approvals.describe([('bash', {'command': 'ls -la', 'cwd': '/home/me/proj', 'timeout_seconds': None, 'keep': None})])

    assert '$ ls -la (in /home/me/proj)' in text
    assert 'this command' in text


def test_several_calls_are_all_listed_in_order():
    text = approvals.describe([('bash', {'command': 'make test'}), ('set_reminder', {'description': 'x', 'when': 'y'}), ('bash', {'command': 'rm -rf build'})])

    assert '3 calls together' in text
    assert text.index('make test') < text.index('set_reminder') < text.index('rm -rf build')


def test_a_long_command_is_cut_for_display_and_says_how_much_runs_anyway():
    command = 'echo ' + 'x' * 10_000

    text = approvals.describe([('bash', {'command': command})])

    assert len(text) < 4096
    assert 'cut for display' in text and 'more characters will run' in text
    assert 'x' * 5000 not in text


def test_many_long_calls_still_fit_in_one_telegram_message():
    calls = [('bash', {'command': 'y' * 5000}) for _ in range(8)]

    assert len(approvals.describe(calls)) < 4096


def test_a_short_command_is_not_marked_as_cut():
    assert 'cut for display' not in approvals.describe([('bash', {'command': 'pwd'})])


def test_waiting_lists_only_the_requests_of_that_chat_oldest_first():
    async def run():
        first = approvals.create(5, None, never)
        other = approvals.create(6, None, never)
        second = approvals.create(5, None, never)
        found = approvals.waiting(5), approvals.waiting(6), approvals.waiting(7)
        approvals.cancel_all()
        return first, other, second, found

    first, other, second, (five, six, seven) = asyncio.run(run())

    assert five == [first, second] and six == [other] and seven == []
    assert first.notified is False
