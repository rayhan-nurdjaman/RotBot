import asyncio
import json
from types import SimpleNamespace

import pytest

import assistant as Assistant
from instance import Instance


def tool_call(call_id, name='get_time', arguments='{}'):
    return SimpleNamespace(id=call_id, function=SimpleNamespace(name=name, arguments=arguments))


def tool_calls_response(*calls):
    return SimpleNamespace(
        finish_reason='tool_calls',
        message=SimpleNamespace(content=None, tool_calls=list(calls)),
    )


def final_response(content='done', finish_reason='stop'):
    return SimpleNamespace(
        finish_reason=finish_reason,
        message=SimpleNamespace(content=content, tool_calls=None),
    )


class FakeLLM:
    """Plays back scripted responses. Once the script runs out, repeats `default` if set."""

    def __init__(self, script, default=None):
        self.script = list(script)
        self.default = default
        self.calls = []
        self.active = 0
        self.max_active = 0

    async def create(self, messages, model, tools=None, chat_id=None):
        self.calls.append({'messages': list(messages), 'tools': tools})
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        await asyncio.sleep(0)
        self.active -= 1
        return self.script.pop(0) if self.script else self.default


@pytest.fixture
def instance(tmp_path, monkeypatch):
    monkeypatch.setattr('instance.memory_file', str(tmp_path / 'memory.json'))
    return Instance('test')


def install(monkeypatch, llm, tool_fn=None):
    async def default_tool(instance, name, arguments):
        return {'tool': name, 'args': arguments}

    monkeypatch.setattr(Assistant.ChatCompletion, 'create', llm.create)
    monkeypatch.setattr(Assistant.ToolManager, 'get_tool_response', tool_fn or default_tool)


def roles(instance):
    return [m['role'] for m in instance.context_window]


def test_parallel_tool_calls_are_grouped_in_order(instance, monkeypatch):
    llm = FakeLLM([tool_calls_response(tool_call('a'), tool_call('b'), tool_call('c')), final_response('ok')])
    install(monkeypatch, llm)

    result = asyncio.run(Assistant.get_response(instance))

    assert result == 'ok'
    assert roles(instance) == ['assistant', 'tool', 'tool', 'tool', 'assistant']
    window = instance.context_window
    assert [c['id'] for c in window[0]['tool_calls']] == ['a', 'b', 'c']
    assert [m['tool_call_id'] for m in window[1:4]] == ['a', 'b', 'c']


def test_results_keep_call_order_even_if_tools_finish_out_of_order(instance, monkeypatch):
    async def slow_first(instance, name, arguments):
        await asyncio.sleep(0.02 if arguments['n'] == 1 else 0)
        return {'n': arguments['n']}

    calls = [tool_call('a', arguments='{"n": 1}'), tool_call('b', arguments='{"n": 2}')]
    install(monkeypatch, FakeLLM([tool_calls_response(*calls), final_response()]), slow_first)

    asyncio.run(Assistant.get_response(instance))

    assert [m['tool_call_id'] for m in instance.context_window[1:3]] == ['a', 'b']
    assert [json.loads(m['content'])['n'] for m in instance.context_window[1:3]] == [1, 2]


def test_iteration_cap_forces_final_answer_without_tools(instance, monkeypatch):
    llm = FakeLLM([], default=tool_calls_response(tool_call('x')))
    # The forced final call is the one made without tools; make it return a real answer.
    original_create = llm.create

    async def create(messages, model, tools=None, chat_id=None):
        response = await original_create(messages, model, tools, chat_id)
        return final_response('forced') if tools is None else response

    llm.create = create
    install(monkeypatch, llm)

    result = asyncio.run(Assistant.get_response(instance))

    assert result == 'forced'
    assert len(llm.calls) == Assistant.max_tool_steps + 1
    assert all(call['tools'] for call in llm.calls[:-1])
    assert llm.calls[-1]['tools'] is None


def test_length_finish_returns_partial_content(instance, monkeypatch):
    install(monkeypatch, FakeLLM([final_response('partial ans', finish_reason='length')]))
    assert asyncio.run(Assistant.get_response(instance)) == 'partial ans'


@pytest.mark.parametrize('finish_reason', ['length', 'content_filter', 'error', 'something_new'])
def test_unusable_finish_returns_fallback_and_stores_nothing(instance, monkeypatch, finish_reason):
    install(monkeypatch, FakeLLM([final_response(None, finish_reason=finish_reason)]))

    assert asyncio.run(Assistant.get_response(instance)) == Assistant.FALLBACK_MESSAGE
    assert instance.context_window == []


def test_tool_exception_is_returned_to_the_model(instance, monkeypatch):
    async def boom(instance, name, arguments):
        raise RuntimeError('kaput')

    install(monkeypatch, FakeLLM([tool_calls_response(tool_call('a')), final_response('recovered')]), boom)

    assert asyncio.run(Assistant.get_response(instance)) == 'recovered'
    assert json.loads(instance.context_window[1]['content']) == {'error': 'RuntimeError: kaput'}


def test_malformed_json_arguments_are_returned_to_the_model(instance, monkeypatch):
    called = []

    async def tool(instance, name, arguments):
        called.append(name)
        return {}

    install(monkeypatch, FakeLLM([tool_calls_response(tool_call('a', arguments='{nope')), final_response()]), tool)

    asyncio.run(Assistant.get_response(instance))

    assert called == []
    assert 'Invalid JSON' in json.loads(instance.context_window[1]['content'])['error']
    assert json.loads(instance.context_window[0]['tool_calls'][0]['function']['arguments']) == {}


def test_concurrent_requests_on_one_instance_are_serialized(instance, monkeypatch):
    llm = FakeLLM([], default=final_response('ok'))
    install(monkeypatch, llm)

    async def both():
        return await asyncio.gather(Assistant.get_response(instance), Assistant.get_response(instance))

    assert asyncio.run(both()) == ['ok', 'ok']
    assert llm.max_active == 1
