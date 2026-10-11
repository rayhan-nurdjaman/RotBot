import asyncio
import json
from types import SimpleNamespace

import pytest
import telegram

import assistant as Assistant
import client as Client
import main
from instance import Instance

DETAILS = [{'type': 'reasoning.text', 'text': 'thinking...', 'signature': 'sig', 'id': 'r1', 'format': 'anthropic-claude-v1', 'index': 0}]


@pytest.fixture
def instance(tmp_path, monkeypatch):
    monkeypatch.setattr('instance.memory_file', str(tmp_path / 'memory.json'))
    return Instance('chat-1')


# ---- typing indicator ----

@pytest.fixture
def typing_sends(monkeypatch):
    sent = []

    async def send_indicator(instance, indicator):
        sent.append(indicator)

    monkeypatch.setattr(Client, 'send_indicator', send_indicator)
    monkeypatch.setattr(Client, 'TYPING_REFRESH_SECONDS', 0.01)
    return sent


def test_typing_is_refreshed_for_the_whole_body_and_stops_afterwards(typing_sends):
    async def scenario():
        async with Client.typing(SimpleNamespace(unique_id=1)):
            await asyncio.sleep(0.08)
        during = len(typing_sends)
        await asyncio.sleep(0.05)
        return during, len(typing_sends)

    during, after = asyncio.run(scenario())

    assert during >= 3
    assert after == during
    assert set(typing_sends) == {Client.Indicator.GENERATING_RESPONSE}


def test_typing_stops_when_the_body_raises(typing_sends):
    async def scenario():
        with pytest.raises(RuntimeError):
            async with Client.typing(SimpleNamespace(unique_id=1)):
                await asyncio.sleep(0.03)
                raise RuntimeError('boom')
        during = len(typing_sends)
        await asyncio.sleep(0.05)
        return during, len(typing_sends)

    during, after = asyncio.run(scenario())
    assert during >= 1 and after == during


def test_typing_survives_a_failing_indicator_call(monkeypatch):
    calls = []

    async def send_indicator(instance, indicator):
        calls.append(1)
        raise telegram.error.NetworkError('down')

    monkeypatch.setattr(Client, 'send_indicator', send_indicator)
    monkeypatch.setattr(Client, 'TYPING_REFRESH_SECONDS', 0.01)

    async def scenario():
        async with Client.typing(SimpleNamespace(unique_id=1)):
            await asyncio.sleep(0.05)
        return 'body completed'

    assert asyncio.run(scenario()) == 'body completed'
    assert len(calls) >= 2


def test_typing_covers_the_whole_turn_in_on_message(monkeypatch, typing_sends):
    events = []

    async def get_response(instance):
        events.append(('turn started', len(typing_sends)))
        await asyncio.sleep(0.05)
        events.append(('turn ending', len(typing_sends)))
        return 'reply'

    async def send_message(chat_id, message):
        events.append(('sent', len(typing_sends)))

    monkeypatch.setattr(main.Assistant, 'get_response', get_response)
    monkeypatch.setattr(main.Client, 'send_message', send_message)
    monkeypatch.setattr(main, 'instances', {})

    asyncio.run(main.on_message('chat-1', 'alice: hi'))

    assert events[0][1] >= 0
    assert events[1][1] > events[0][1]
    count_when_sent = events[2][1]
    asyncio.run(asyncio.sleep(0.05))
    assert len(typing_sends) == count_when_sent


def test_unused_indicators_are_removed():
    assert {i.name for i in Client.Indicator} == {'GENERATING_RESPONSE', 'REMINDER_CREATED'}


# ---- reasoning through tool loops ----

def tool_call(call_id):
    return SimpleNamespace(id=call_id, function=SimpleNamespace(name='get_time', arguments='{}'))


def tool_response(*calls, details=DETAILS):
    extra = {'reasoning_details': details} if details else {}
    return SimpleNamespace(
        finish_reason='tool_calls',
        message=SimpleNamespace(content='', tool_calls=list(calls), model_extra=extra),
    )


def final_response(text='done'):
    return SimpleNamespace(finish_reason='stop', message=SimpleNamespace(content=text, tool_calls=None, model_extra={}))


def install(monkeypatch, responses):
    sent_requests = []
    queue = iter(responses)

    async def create(messages, model, tools=None, chat_id=None, tool_choice=None):
        sent_requests.append(json.loads(json.dumps(messages)))
        return next(queue)

    async def tool(name, arguments, ctx):
        return {}

    monkeypatch.setattr(Assistant.ChatCompletion, 'create', create)
    monkeypatch.setattr(Assistant.registry, 'call', tool)
    return sent_requests


def test_reasoning_is_passed_back_unchanged_on_the_next_request_in_the_loop(instance, monkeypatch):
    requests = install(monkeypatch, [tool_response(tool_call('a')), final_response()])
    instance.add_user_message('alice: time?')

    asyncio.run(Assistant.get_response(instance))

    second_request_assistant = [m for m in requests[1] if m['role'] == 'assistant'][0]
    assert second_request_assistant['reasoning_details'] == DETAILS
    assert [c['id'] for c in second_request_assistant['tool_calls']] == ['a']


def test_reasoning_is_kept_for_every_round_of_one_loop(instance, monkeypatch):
    second = [{**DETAILS[0], 'text': 'second round'}]
    requests = install(monkeypatch, [
        tool_response(tool_call('a')),
        tool_response(tool_call('b'), details=second),
        final_response(),
    ])
    instance.add_user_message('alice: time?')

    asyncio.run(Assistant.get_response(instance))

    kept = [m['reasoning_details'] for m in requests[2] if m['role'] == 'assistant' and 'reasoning_details' in m]
    assert kept == [DETAILS, second]


def test_reasoning_is_dropped_before_the_next_turn(instance, monkeypatch):
    requests = install(monkeypatch, [tool_response(tool_call('a')), final_response(), final_response('second turn')])
    instance.add_user_message('alice: time?')
    asyncio.run(Assistant.get_response(instance))
    assert any('reasoning_details' in m for m in instance.context_window)

    instance.add_user_message('alice: thanks')
    asyncio.run(Assistant.get_response(instance))

    assert not any('reasoning_details' in m for m in requests[2])
    assert not any('reasoning_details' in m for m in instance.context_window)


def test_model_without_reasoning_details_adds_no_key(instance, monkeypatch):
    install(monkeypatch, [tool_response(tool_call('a'), details=None), final_response()])
    instance.add_user_message('alice: time?')

    asyncio.run(Assistant.get_response(instance))

    assert not any('reasoning_details' in m for m in instance.context_window)


def test_reasoning_text_is_never_sent_to_the_chat(monkeypatch, typing_sends):
    install(monkeypatch, [tool_response(tool_call('a')), final_response('visible answer')])
    sent = []

    async def send_message(chat_id, message):
        sent.append(message)

    monkeypatch.setattr(main.Client, 'send_message', send_message)
    monkeypatch.setattr(main, 'instances', {})

    asyncio.run(main.on_message('chat-1', 'alice: time?'))

    assert sent == ['visible answer']
