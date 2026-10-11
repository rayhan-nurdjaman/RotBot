from datetime import datetime, timezone

import clock as Clock
from instance import Instance


def fix_time(monkeypatch, *args):
    monkeypatch.setattr(Clock, 'now', lambda: datetime(*args, tzinfo=timezone.utc))


def text_of(message):
    return message['content'][0]['text']


def test_stamp_shows_date_weekday_and_time_in_the_configured_zone(monkeypatch):
    fix_time(monkeypatch, 2026, 10, 11, 13, 37)

    assert Clock.stamp() == '2026-10-11 Sun 21:37'


def test_the_configured_zone_is_used_not_a_fixed_offset(monkeypatch):
    monkeypatch.setattr(Clock, 'zone', Clock.ZoneInfo('America/New_York'))
    fix_time(monkeypatch, 2026, 1, 15, 12, 0)    # winter, UTC-5
    assert Clock.stamp() == '2026-01-15 Thu 07:00'

    fix_time(monkeypatch, 2026, 7, 15, 12, 0)    # summer, UTC-4
    assert Clock.stamp() == '2026-07-15 Wed 08:00'


def test_user_messages_are_stamped_when_stored(monkeypatch):
    fix_time(monkeypatch, 2026, 10, 11, 13, 37)
    instance = Instance('chat-1')

    instance.add_user_message('alice: hi')

    assert text_of(instance.context_window[-1]) == '[2026-10-11 Sun 21:37] alice: hi'


def test_a_stored_stamp_never_changes_as_time_passes(monkeypatch):
    fix_time(monkeypatch, 2026, 10, 11, 13, 37)
    instance = Instance('chat-1')
    instance.add_user_message('alice: hi')

    fix_time(monkeypatch, 2026, 10, 12, 8, 0)
    instance.add_user_message('alice: later')

    assert text_of(instance.get_context()[1]) == '[2026-10-11 Sun 21:37] alice: hi'
    assert text_of(instance.get_context()[2]) == '[2026-10-12 Mon 16:00] alice: later'


def test_the_history_prefix_is_identical_between_requests_so_it_can_be_cached(monkeypatch):
    fix_time(monkeypatch, 2026, 10, 11, 13, 37)
    instance = Instance('chat-1')
    instance.add_user_message('alice: hi')
    instance.add_assistant_message('hello')
    before = instance.get_context()

    fix_time(monkeypatch, 2026, 10, 11, 13, 55)
    instance.add_user_message('alice: again')
    after = instance.get_context()

    # Everything before the new message is byte-identical, apart from the cache breakpoint moving.
    strip = lambda ctx: [{**m, 'content': [{k: v for k, v in p.items() if k != 'cache_control'} for p in m['content']]} for m in ctx]
    assert strip(after)[:len(before)] == strip(before)


def test_stamp_applies_to_image_messages_and_not_to_empty_text(monkeypatch):
    fix_time(monkeypatch, 2026, 10, 11, 13, 37)
    instance = Instance('chat-1')

    instance.add_user_message('alice: look', 'data:image/jpeg;base64,AAAA')
    instance.add_user_message('', 'data:image/jpeg;base64,AAAA')

    assert text_of(instance.context_window[0]) == '[2026-10-11 Sun 21:37] alice: look'
    assert [p['type'] for p in instance.context_window[1]['content']] == ['image_url']


def test_assistant_messages_are_not_stamped():
    instance = Instance('chat-1')
    instance.add_assistant_message('hello')

    assert text_of(instance.context_window[-1]) == 'hello'


def test_the_time_is_not_in_the_system_prompt_so_it_cannot_break_the_cache(monkeypatch):
    instance = Instance('chat-1')
    fix_time(monkeypatch, 2026, 10, 11, 13, 37)
    first = instance.get_system_prompt()
    fix_time(monkeypatch, 2026, 10, 11, 13, 38)

    assert instance.get_system_prompt() == first


def test_the_system_prompt_names_the_timezone_and_the_timestamp_format():
    prompt = Instance('chat-1').get_system_prompt()[0]['content'][0]['text']

    assert '{timezone}' not in prompt
    assert 'Asia/Singapore' in prompt
    assert 'timestamp' in prompt

