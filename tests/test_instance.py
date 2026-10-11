import pytest

from config import config
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


def chunk_of(instance):
    return max(1, int(instance.context_length * config['context_trim_fraction']))


@pytest.mark.parametrize('context_length', range(1, 8))
def test_trim_never_orphans_tool_messages(context_length):
    instance = Instance('test')
    instance.context_length = context_length
    assistant_message, results = tool_call_group(3)

    for message in [user('hi'), assistant_message, *results, user('again'), assistant_message, *results]:
        instance.add_to_context(message)
        assert len(instance.context_window) <= context_length + chunk_of(instance)
        assert_no_orphans_if_complete(instance.context_window)


def assert_no_orphans_if_complete(window):
    # A window can end mid-group while a tool round is being appended message by message in add_to_context,
    # but it must never start with a tool message.
    assert not window or window[0]['role'] != 'tool'


def test_trim_keeps_whole_group_when_it_fits():
    instance = Instance('test')
    instance.context_length = 4
    assistant_message, results = tool_call_group(3)

    for message in [user('hi'), assistant_message, *results]:
        instance.add_to_context(message)

    assert [m['role'] for m in instance.context_window] == ['user', 'assistant', 'tool', 'tool', 'tool']


def test_trim_drops_every_leading_tool_message():
    instance = Instance('test')
    instance.context_length = 3
    assistant_message, results = tool_call_group(3)

    for message in [user('hi'), assistant_message, *results]:
        instance.add_to_context(message)

    assert instance.context_window == []


def test_trim_round_with_three_parallel_results_ends_with_complete_group():
    instance = Instance('test')
    instance.context_length = 4
    assistant_message, results = tool_call_group(3)
    for i in range(6):
        instance.add_user_message(f'alice: {i}')
    instance.add_tool_round([(f'call_{i}', 'get_time', {}, {}) for i in range(3)])

    assert_no_orphans(instance.context_window[-4:])
    assert instance.context_window[0]['role'] != 'tool'


def test_trim_happens_in_chunks_so_the_front_of_the_history_is_stable():
    instance = Instance('test')
    instance.context_length = 8
    chunk = chunk_of(instance)
    fronts = []

    for i in range(40):
        instance.add_user_message(f'alice: {i}')
        fronts.append(instance.context_window[0]['content'][0]['text'])

    changes = sum(1 for a, b in zip(fronts, fronts[1:]) if a != b)
    # A one-at-a-time sliding window would change the front on every message once full (~32 times).
    assert changes <= 40 // chunk
    assert len(instance.context_window) <= instance.context_length + chunk


def test_trim_cuts_back_to_context_length_when_over_the_chunk():
    instance = Instance('test')
    instance.context_length = 6
    for i in range(6 + chunk_of(instance) + 1):
        instance.add_user_message(f'alice: {i}')

    assert len(instance.context_window) == 6
    assert instance.context_window[-1]['content'][0]['text'].endswith(f'alice: {6 + chunk_of(instance)}')
