import asyncio

import pytest

import main
from instance import Instance


def test_get_instance_creates_once_per_id(monkeypatch):
    created = []

    class CountingInstance(Instance):
        def __init__(self, unique_id):
            created.append(unique_id)
            super().__init__(unique_id)

    monkeypatch.setattr(main, 'Instance', CountingInstance)
    monkeypatch.setattr(main, 'instances', {})

    first = main.get_instance('chat-1')
    assert main.get_instance('chat-1') is first
    assert created == ['chat-1']


def test_unknown_personality_reports_does_not_exist(monkeypatch):
    sent = []

    async def send_message(chat_id, message):
        sent.append(message)

    monkeypatch.setattr(main.Client, 'send_message', send_message)
    monkeypatch.setattr(main, 'instances', {})

    asyncio.run(main.set_instance_personality('chat-1', 'alien'))

    assert sent == ["_The personality 'alien' does not exist._"]


def test_help_lists_every_registered_command(monkeypatch):
    import re
    import client

    sent = []

    async def send_message(chat_id, message):
        sent.append((chat_id, message))

    monkeypatch.setattr(main.Client, 'send_message', send_message)
    registered = {
        command
        for handler in client.application.handlers[0]
        if isinstance(handler, client.telegram.ext.CommandHandler)
        for command in handler.commands
    }

    asyncio.run(main.show_help('chat-1'))

    assert registered
    assert set(re.findall(r'^/(\w+)', sent[0][1], re.M)) == registered
    assert sent[0][0] == 'chat-1'


def test_schizo_without_an_argument_lists_personalities_and_changes_nothing(monkeypatch):
    sent = []

    async def send_message(chat_id, message):
        sent.append(message)

    monkeypatch.setattr(main.Client, 'send_message', send_message)
    monkeypatch.setattr(main, 'instances', {})
    instance = main.get_instance('chat-1')
    instance.set_personality('british')
    before = snapshot(instance)

    asyncio.run(main.set_instance_personality('chat-1', ''))

    assert snapshot(instance) == before
    assert len(sent) == 1
    assert "'british'" in sent[0]
    for key in main.personality_index:
        assert key in sent[0]


def test_should_respond_always_true():
    assert asyncio.run(main.AlwaysOn.should_respond([])) is True


def persona(key):
    from instance import _read_persona
    return _read_persona(key)


def snapshot(instance):
    return (instance.personality, instance.context_length, instance.custom_instruction, instance.system_prompt, instance.modalities)


def test_a_new_chat_runs_as_rotbot():
    instance = Instance('chat-1')

    assert instance.personality == 'rotbot'
    assert instance.context_length == persona('rotbot')['context_length']
    assert instance.custom_instruction == persona('rotbot')['custom_instruction'] != ''
    assert instance.system_prompt == persona('base')['system_prompt']


def test_switching_personality_keeps_nothing_of_the_previous_one():
    instance = Instance('chat-1')
    instance.set_personality('british')
    instance.set_personality('lockedin')

    assert instance.personality == 'lockedin'
    assert instance.custom_instruction == persona('lockedin')['custom_instruction']
    assert instance.context_length == persona('lockedin')['context_length']

    instance.set_personality('base')
    assert instance.personality == 'base'
    assert instance.custom_instruction == persona('base')['custom_instruction']
    assert instance.context_length == persona('base')['context_length']


def test_a_failed_schizo_keeps_the_last_applied_personality(monkeypatch):
    sent = []

    async def send_message(chat_id, message):
        sent.append(message)

    monkeypatch.setattr(main.Client, 'send_message', send_message)
    monkeypatch.setattr(main, 'instances', {})
    instance = main.get_instance('chat-1')
    asyncio.run(main.set_instance_personality('chat-1', 'british'))
    before = snapshot(instance)
    assert before[0] == 'british'

    asyncio.run(main.set_instance_personality('chat-1', 'nope'))

    assert snapshot(instance) == before
    assert sent[-1] == "_The personality 'nope' does not exist._"


def test_a_personality_in_the_index_with_a_missing_file_changes_nothing(monkeypatch):
    import instance as instance_module

    monkeypatch.setitem(instance_module.personality_index, 'ghost', 'ghost.yaml')
    instance = Instance('chat-1')
    instance.set_personality('british')
    before = snapshot(instance)

    with pytest.raises(FileNotFoundError):
        instance.set_personality('ghost')

    assert snapshot(instance) == before
