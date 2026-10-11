import asyncio

import pytest

import tool_manager as ToolManager
from instance import Instance


@pytest.fixture(autouse=True)
def isolated_memory_file(tmp_path, monkeypatch):
    monkeypatch.setattr('instance.memory_file', str(tmp_path / 'data' / 'memory.json'))


def system_prompt(instance):
    return instance.get_system_prompt()[0]['content'][0]['text']


def test_saved_memory_is_visible_to_a_fresh_instance_of_the_same_chat():
    first = Instance('chat-1')
    result = asyncio.run(ToolManager.get_tool_response(first, 'save_to_memory', {'user_info': 'alice likes tea'}))

    assert isinstance(result, dict)
    assert result['memory'] == 'alice likes tea'

    fresh = Instance('chat-1')
    assert 'alice likes tea' in system_prompt(fresh)
    assert 'MEMORY' not in system_prompt(fresh)


def test_memory_is_per_chat():
    Instance('chat-1').add_memory('alice likes tea')

    assert 'alice likes tea' not in system_prompt(Instance('chat-2'))


def test_memory_accumulates():
    instance = Instance('chat-1')
    instance.add_memory('one')
    instance.add_memory('two')

    prompt = system_prompt(Instance('chat-1'))
    assert '- one\n- two' in prompt


def test_empty_memory_renders_none():
    assert 'Current memory: None' in system_prompt(Instance('chat-1'))
