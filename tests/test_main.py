import asyncio

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


def test_should_respond_always_true():
    assert asyncio.run(main.AlwaysOn.should_respond([])) is True
