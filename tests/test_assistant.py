import asyncio
import json
from types import SimpleNamespace

import httpx
import openai
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

    async def create(self, messages, model, tools=None, chat_id=None, tool_choice=None):
        self.calls.append({'messages': list(messages), 'tools': tools, 'tool_choice': tool_choice})
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


def test_iteration_cap_forces_a_text_final_answer_without_changing_the_tool_list(instance, monkeypatch):
    llm = FakeLLM([], default=tool_calls_response(tool_call('x')))
    # The forced final call is the one made without tools; make it return a real answer.
    original_create = llm.create

    async def create(messages, model, tools=None, chat_id=None, tool_choice=None):
        response = await original_create(messages, model, tools, chat_id, tool_choice)
        return final_response('forced') if tool_choice == 'none' else response

    llm.create = create
    install(monkeypatch, llm)

    result = asyncio.run(Assistant.get_response(instance))

    assert result == 'forced'
    assert len(llm.calls) == Assistant.max_tool_steps + 1
    assert all(call['tool_choice'] is None for call in llm.calls[:-1])
    # The tool list stays identical (so the prompt cache prefix survives); tool_choice forbids another call.
    assert all(call['tools'] == Assistant.tools for call in llm.calls)
    assert llm.calls[-1]['tool_choice'] == 'none'


def test_length_finish_returns_partial_content(instance, monkeypatch):
    install(monkeypatch, FakeLLM([final_response('partial ans', finish_reason='length')]))
    assert asyncio.run(Assistant.get_response(instance)) == 'partial ans'


@pytest.mark.parametrize('finish_reason, expected', [
    ('length', Assistant.LENGTH_MESSAGE),
    ('content_filter', Assistant.REFUSAL_MESSAGE),
    ('error', Assistant.FALLBACK_MESSAGE),
    ('something_new', Assistant.FALLBACK_MESSAGE),
])
def test_unusable_finish_returns_specific_message_and_stores_nothing(instance, monkeypatch, finish_reason, expected):
    install(monkeypatch, FakeLLM([final_response(None, finish_reason=finish_reason)]))

    assert asyncio.run(Assistant.get_response(instance)) == expected
    assert instance.context_window == []


def test_refusal_on_a_stop_with_no_content_is_reported_as_a_refusal(instance, monkeypatch):
    response = final_response(None)
    response.message.refusal = 'I cannot help with that'
    install(monkeypatch, FakeLLM([response]))

    assert asyncio.run(Assistant.get_response(instance)) == Assistant.REFUSAL_MESSAGE


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


def api_error(cls, status=None):
    request = httpx.Request('POST', 'https://openrouter.ai/api/v1/chat/completions')
    if cls is openai.APITimeoutError:
        return cls(request=request)
    if cls is openai.APIConnectionError:
        return cls(request=request)
    return cls('boom', response=httpx.Response(status, request=request), body=None)


@pytest.mark.parametrize('error, expected', [
    (api_error(openai.RateLimitError, 429), Assistant.RATE_LIMIT_MESSAGE),
    (api_error(openai.InternalServerError, 502), Assistant.PROVIDER_MESSAGE),
    (api_error(openai.APITimeoutError), Assistant.PROVIDER_MESSAGE),
    (api_error(openai.APIConnectionError), Assistant.PROVIDER_MESSAGE),
    (api_error(openai.APIStatusError, 402), Assistant.CREDITS_MESSAGE),
    (api_error(openai.BadRequestError, 400), Assistant.REQUEST_MESSAGE),
])
def test_llm_errors_become_short_messages_and_keep_the_users_message(instance, monkeypatch, error, expected):
    async def create(messages, model, tools=None, chat_id=None, tool_choice=None):
        raise error

    monkeypatch.setattr(Assistant.ChatCompletion, 'create', create)
    instance.add_user_message('alice: hi')

    assert asyncio.run(Assistant.get_response(instance)) == expected
    assert roles(instance) == ['user']


def test_insufficient_credits_is_logged_loudly(instance, monkeypatch, caplog):
    async def create(messages, model, tools=None, chat_id=None, tool_choice=None):
        raise api_error(openai.APIStatusError, 402)

    monkeypatch.setattr(Assistant.ChatCompletion, 'create', create)
    asyncio.run(Assistant.get_response(instance))

    assert any(r.levelname == 'CRITICAL' for r in caplog.records)


def test_error_mid_tool_loop_does_not_leave_a_half_written_round(instance, monkeypatch):
    responses = iter([tool_calls_response(tool_call('a'))])

    async def create(messages, model, tools=None, chat_id=None, tool_choice=None):
        try:
            return next(responses)
        except StopIteration:
            raise api_error(openai.InternalServerError, 503)

    async def tool(instance, name, arguments):
        return {}

    monkeypatch.setattr(Assistant.ChatCompletion, 'create', create)
    monkeypatch.setattr(Assistant.ToolManager, 'get_tool_response', tool)

    assert asyncio.run(Assistant.get_response(instance)) == Assistant.PROVIDER_MESSAGE
    assert roles(instance) == ['assistant', 'tool']


def test_slow_tool_times_out_and_the_model_gets_an_error(instance, monkeypatch):
    async def hang(instance, name, arguments):
        await asyncio.sleep(10)

    monkeypatch.setattr(Assistant, 'tool_timeout', 0.01)
    install(monkeypatch, FakeLLM([tool_calls_response(tool_call('a')), final_response('moved on')]), hang)

    assert asyncio.run(Assistant.get_response(instance)) == 'moved on'
    assert 'timed out' in json.loads(instance.context_window[1]['content'])['error']


def test_one_slow_tool_does_not_block_the_others_from_returning(instance, monkeypatch):
    async def tool(instance, name, arguments):
        if arguments['slow']:
            await asyncio.sleep(10)
        return {'ok': True}

    monkeypatch.setattr(Assistant, 'tool_timeout', 0.01)
    calls = [tool_call('a', arguments='{"slow": true}'), tool_call('b', arguments='{"slow": false}')]
    install(monkeypatch, FakeLLM([tool_calls_response(*calls), final_response()]), tool)

    asyncio.run(Assistant.get_response(instance))

    results = [json.loads(m['content']) for m in instance.context_window[1:3]]
    assert 'timed out' in results[0]['error']
    assert results[1] == {'ok': True}


def test_sdk_client_has_timeout_and_bounded_retries():
    from chat_completion import openai_client
    assert openai_client.max_retries == Assistant.config['llm_max_retries']
    assert openai_client.timeout == Assistant.config['llm_timeout_s']
