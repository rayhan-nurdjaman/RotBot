import pytest

from instance import Instance


def tool_call_group(n):
    calls = [
        {'id': f'call_{i}', 'type': 'function', 'function': {'name': 'get_time', 'arguments': '{}'}}
        for i in range(n)
    ]
    results = [
        {'role': 'tool', 'tool_call_id': f'call_{i}', 'content': '{}'}
        for i in range(n)
    ]
    return {'role': 'assistant', 'tool_calls': calls}, results


def assert_no_orphans(window):
    # Every tool message must be preceded by its assistant tool_calls message,
    # and every tool_calls message must be followed by all of its results.
    expected_ids = []
    for index, message in enumerate(window):
        if message['role'] == 'tool':
            assert message['tool_call_id'] in expected_ids, f'orphaned tool message at {index}'
            expected_ids.remove(message['tool_call_id'])
        else:
            assert not expected_ids, f'tool_calls missing results before index {index}'
            expected_ids = [call['id'] for call in message.get('tool_calls', [])]
    assert not expected_ids, 'tool_calls missing results at end of window'


def user(text):
    return {'role': 'user', 'content': [{'type': 'text', 'text': text}]}


@pytest.mark.parametrize('context_length', range(1, 8))
def test_trim_never_orphans_tool_messages(context_length):
    instance = Instance('test')
    instance.context_length = context_length
    assistant_message, results = tool_call_group(3)

    for message in [user('hi'), assistant_message, *results]:
        instance.add_to_context(message)
        assert len(instance.context_window) <= context_length

    assert_no_orphans(instance.context_window)


def test_trim_keeps_whole_group_when_it_fits():
    instance = Instance('test')
    instance.context_length = 4
    assistant_message, results = tool_call_group(3)

    for message in [user('hi'), assistant_message, *results]:
        instance.add_to_context(message)

    assert [m['role'] for m in instance.context_window] == ['assistant', 'tool', 'tool', 'tool']


def test_trim_drops_every_leading_tool_message():
    instance = Instance('test')
    instance.context_length = 3
    assistant_message, results = tool_call_group(3)

    for message in [user('hi'), assistant_message, *results]:
        instance.add_to_context(message)

    assert instance.context_window == []
