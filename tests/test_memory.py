import asyncio

import pytest

from instance import Instance
from tools.native import registry
from tools.registry import ToolContext


@pytest.fixture(autouse=True)
def isolated_memory_file(tmp_path, monkeypatch):
    monkeypatch.setattr('instance.memory_file', str(tmp_path / 'data' / 'memory.json'))


def system_prompt(instance):
    return instance.get_system_prompt()[0]['content'][0]['text']


def request_text(instance):
    return '\n'.join(
        part['text'] for message in instance.get_context() for part in message['content'] if part['type'] == 'text'
    )


def test_saved_memory_is_visible_to_a_fresh_instance_of_the_same_chat():
    first = Instance('chat-1')
    result = asyncio.run(registry.call('save_to_memory', {'user_info': 'alice likes tea'}, ToolContext(first, None)))

    assert result['ok'] is True
    assert result['memory'] == 'alice likes tea'

    fresh = Instance('chat-1')
    assert 'alice likes tea' in request_text(fresh)
    assert 'MEMORY' not in request_text(fresh)


def test_memory_is_per_chat():
    Instance('chat-1').add_memory('alice likes tea')

    assert 'alice likes tea' not in request_text(Instance('chat-2'))


def test_memory_accumulates():
    instance = Instance('chat-1')
    instance.add_memory('one')
    instance.add_memory('two')

    assert '- one\n- two' in request_text(Instance('chat-1'))


def test_empty_memory_adds_nothing_to_the_request():
    instance = Instance('chat-1')
    instance.add_user_message('alice: hi')

    assert instance.get_memory_messages() == []
    assert [m['role'] for m in instance.get_context()] == ['system', 'user']


def test_memory_is_not_in_the_system_prompt_so_it_cannot_break_the_cache():
    instance = Instance('chat-1')
    before = system_prompt(instance)
    instance.add_memory('alice likes tea')
    assert system_prompt(instance) == before


def test_memory_message_goes_right_after_the_system_prompt_and_is_not_stored_in_the_context():
    instance = Instance('chat-1')
    instance.add_memory('alice likes tea')
    instance.add_user_message('alice: hi')

    context = instance.get_context()

    assert [m['role'] for m in context] == ['system', 'user', 'user']
    assert 'alice likes tea' in context[1]['content'][0]['text']
    assert 'alice: hi' in context[2]['content'][0]['text']
    assert all('alice likes tea' not in str(m) for m in instance.context_window)
