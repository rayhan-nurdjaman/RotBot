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
def instance():
    return Instance('test')


def install(monkeypatch, llm, tool_fn=None):
    async def default_tool(name, arguments, ctx):
        return {'tool': name, 'args': arguments}

    monkeypatch.setattr(Assistant.ChatCompletion, 'create', llm.create)
    monkeypatch.setattr(Assistant.registry, 'call', tool_fn or default_tool)


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
    async def slow_first(name, arguments, ctx):
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
    async def boom(name, arguments, ctx):
        raise RuntimeError('kaput')

    install(monkeypatch, FakeLLM([tool_calls_response(tool_call('a')), final_response('recovered')]), boom)

    assert asyncio.run(Assistant.get_response(instance)) == 'recovered'
    assert json.loads(instance.context_window[1]['content']) == {'ok': False, 'error': 'RuntimeError: kaput'}


def test_malformed_json_arguments_are_returned_to_the_model(instance, monkeypatch):
    called = []

    async def tool(name, arguments, ctx):
        called.append(name)
        return {}

    install(monkeypatch, FakeLLM([tool_calls_response(tool_call('a', arguments='{nope')), final_response()]), tool)

    asyncio.run(Assistant.get_response(instance))

    assert called == []
    assert 'Invalid JSON' in json.loads(instance.context_window[1]['content'])['error']
    assert json.loads(instance.context_window[0]['tool_calls'][0]['function']['arguments']) == {}


def test_concurrent_messages_on_one_chat_are_handled_one_turn_at_a_time(monkeypatch):
    import main
    llm = FakeLLM([], default=final_response('ok'))
    install(monkeypatch, llm)
    monkeypatch.setattr(main, 'instances', {})

    async def indicator(instance, indicator):
        pass

    async def send_message(chat_id, message):
        pass

    monkeypatch.setattr(main.Client, 'send_indicator', indicator)
    monkeypatch.setattr(main.Client, 'send_message', send_message)

    async def both():
        await asyncio.gather(main.on_message('chat-1', 'alice: one'), main.on_message('chat-1', 'alice: two'))

    asyncio.run(both())

    assert llm.max_active == 1
    assert [m['role'] for m in main.get_instance('chat-1').get_context() if m['role'] != 'system'] == ['user', 'assistant', 'user', 'assistant']


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

    async def tool(name, arguments, ctx):
        return {}

    monkeypatch.setattr(Assistant.ChatCompletion, 'create', create)
    monkeypatch.setattr(Assistant.registry, 'call', tool)

    assert asyncio.run(Assistant.get_response(instance)) == Assistant.PROVIDER_MESSAGE
    assert roles(instance) == ['assistant', 'tool']


def test_slow_tool_times_out_and_the_model_gets_an_error(instance, monkeypatch):
    async def hang(name, arguments, ctx):
        await asyncio.sleep(10)

    monkeypatch.setattr(Assistant, 'tool_timeout', 0.01)
    install(monkeypatch, FakeLLM([tool_calls_response(tool_call('a')), final_response('moved on')]), hang)

    assert asyncio.run(Assistant.get_response(instance)) == 'moved on'
    assert 'timed out' in json.loads(instance.context_window[1]['content'])['error']


def test_a_tool_with_its_own_limit_is_not_cut_off_at_the_default(instance, monkeypatch):
    async def slow(name, arguments, ctx):
        await asyncio.sleep(0.05)
        return {'ok': True}

    monkeypatch.setattr(Assistant, 'tool_timeout', 0.01)
    monkeypatch.setattr(Assistant.registry, 'timeout', lambda name: 5)
    install(monkeypatch, FakeLLM([tool_calls_response(tool_call('a')), final_response('done')]), slow)

    assert asyncio.run(Assistant.get_response(instance)) == 'done'
    assert json.loads(instance.context_window[1]['content']) == {'ok': True}


def test_a_tool_with_its_own_limit_is_cut_off_at_that_limit(instance, monkeypatch):
    async def hang(name, arguments, ctx):
        await asyncio.sleep(10)

    monkeypatch.setattr(Assistant.registry, 'timeout', lambda name: 0.01)
    install(monkeypatch, FakeLLM([tool_calls_response(tool_call('a')), final_response('moved on')]), hang)

    assert asyncio.run(Assistant.get_response(instance)) == 'moved on'
    assert 'timed out after 0.01 seconds' in json.loads(instance.context_window[1]['content'])['error']


def test_one_slow_tool_does_not_block_the_others_from_returning(instance, monkeypatch):
    async def tool(name, arguments, ctx):
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


# ---- approval: a step with a call that needs it is suspended (issue #43)

import access as Access
import approvals as Approvals

APPROVAL_CHAT = 'op-chat'


@pytest.fixture
def op_instance(monkeypatch):
    monkeypatch.setattr(Access, '_op_chats', frozenset({APPROVAL_CHAT}))
    monkeypatch.setattr(Approvals, '_day_approved', {})
    return Instance(APPROVAL_CHAT)


def bash_call(call_id='b1', command='ls'):
    return tool_call(call_id, name='bash', arguments=json.dumps({'command': command, 'cwd': None, 'timeout_seconds': None, 'keep': None}))


def run_tools(monkeypatch):
    ran = []

    async def tool_fn(name, arguments, ctx):
        ran.append((name, arguments))
        return {'tool': name}

    monkeypatch.setattr(Assistant.registry, 'call', tool_fn)
    return ran


def test_a_step_with_bash_in_an_op_chat_is_suspended_and_nothing_runs(op_instance, monkeypatch):
    ran = run_tools(monkeypatch)
    llm = FakeLLM([tool_calls_response(bash_call(command='make test'))])
    monkeypatch.setattr(Assistant.ChatCompletion, 'create', llm.create)
    op_instance.add_user_message('run the tests')
    before = list(op_instance.context_window)

    result = asyncio.run(Assistant.get_response(op_instance))

    assert isinstance(result, Assistant.Suspended)
    assert result.calls == [('bash', {'command': 'make test', 'cwd': None, 'timeout_seconds': None, 'keep': None})]
    assert ran == []
    assert op_instance.context_window == before
    assert len(llm.calls) == 1


def test_approving_runs_every_call_of_the_step_and_the_turn_carries_on(op_instance, monkeypatch):
    ran = run_tools(monkeypatch)
    llm = FakeLLM([tool_calls_response(bash_call('a'), tool_call('b', name='list_reminders'), bash_call('c', command='pwd')), final_response('all done')])
    monkeypatch.setattr(Assistant.ChatCompletion, 'create', llm.create)
    op_instance.add_user_message('go')
    suspended = asyncio.run(Assistant.get_response(op_instance))

    result = asyncio.run(Assistant.resume(op_instance, suspended))

    assert result == 'all done'
    assert sorted(name for name, _ in ran) == ['bash', 'bash', 'list_reminders']
    assert roles(op_instance) == ['user', 'assistant', 'tool', 'tool', 'tool', 'assistant']
    assert [m['tool_call_id'] for m in op_instance.context_window[2:5]] == ['a', 'b', 'c']
    assert len(llm.calls) == 2


def test_denying_runs_nothing_and_tells_the_model_for_every_call(op_instance, monkeypatch):
    ran = run_tools(monkeypatch)
    llm = FakeLLM([tool_calls_response(bash_call('a'), tool_call('b', name='list_reminders')), final_response('ok, not running it')])
    monkeypatch.setattr(Assistant.ChatCompletion, 'create', llm.create)
    op_instance.add_user_message('go')
    suspended = asyncio.run(Assistant.get_response(op_instance))

    result = asyncio.run(Assistant.resume(op_instance, suspended, refusal='The user denied it.'))

    assert result == 'ok, not running it'
    assert ran == []
    results = [json.loads(m['content']) for m in op_instance.context_window[2:4]]
    assert results == [{'ok': False, 'error': 'The user denied it.'}] * 2
    assert roles(op_instance) == ['user', 'assistant', 'tool', 'tool', 'assistant']
    assert len(llm.calls) == 2


def test_a_step_with_only_other_tools_is_not_suspended(op_instance, monkeypatch):
    run_tools(monkeypatch)
    llm = FakeLLM([tool_calls_response(tool_call('a', name='list_reminders')), final_response('fine')])
    monkeypatch.setattr(Assistant.ChatCompletion, 'create', llm.create)

    assert asyncio.run(Assistant.get_response(op_instance)) == 'fine'


def test_a_chat_that_is_not_an_op_chat_is_not_suspended(monkeypatch):
    monkeypatch.setattr(Access, '_op_chats', frozenset())
    ran = run_tools(monkeypatch)
    llm = FakeLLM([tool_calls_response(bash_call()), final_response('it said not enabled')])
    monkeypatch.setattr(Assistant.ChatCompletion, 'create', llm.create)

    assert asyncio.run(Assistant.get_response(Instance('elsewhere'))) == 'it said not enabled'
    assert [name for name, _ in ran] == ['bash']


def test_a_day_approval_lets_the_step_run_at_once_and_logs_it(op_instance, monkeypatch, caplog):
    import logging
    ran = run_tools(monkeypatch)
    llm = FakeLLM([tool_calls_response(bash_call(command='df -h')), final_response('ran it')])
    monkeypatch.setattr(Assistant.ChatCompletion, 'create', llm.create)
    monkeypatch.setattr(Approvals, '_day_approved', {APPROVAL_CHAT: 4_000_000_000})

    with caplog.at_level(logging.INFO):
        result = asyncio.run(Assistant.get_response(op_instance))

    assert result == 'ran it'
    assert [name for name, _ in ran] == ['bash']
    assert 'approved everything for the day' in caplog.text and 'df -h' in caplog.text


def test_a_day_approval_of_another_chat_does_not_count(op_instance, monkeypatch):
    run_tools(monkeypatch)
    monkeypatch.setattr(Assistant.ChatCompletion, 'create', FakeLLM([tool_calls_response(bash_call())]).create)
    monkeypatch.setattr(Approvals, '_day_approved', {'other-chat': 4_000_000_000})

    assert isinstance(asyncio.run(Assistant.get_response(op_instance)), Assistant.Suspended)


def test_the_model_and_the_reasoning_blocks_survive_the_wait(op_instance, monkeypatch):
    from models import parse_model
    run_tools(monkeypatch)
    first = tool_calls_response(bash_call())
    first.message.model_extra = {'reasoning_details': [{'type': 'reasoning.text', 'text': 'thinking'}]}
    llm = FakeLLM([first, final_response('done')])
    models = []
    original = llm.create

    async def create(messages, model, tools=None, chat_id=None, tool_choice=None):
        models.append(model)
        return await original(messages, model, tools, chat_id, tool_choice)

    monkeypatch.setattr(Assistant.ChatCompletion, 'create', create)
    op_instance.model = parse_model('sonnet-5.5-medium')
    suspended = asyncio.run(Assistant.get_response(op_instance))
    op_instance.model = parse_model('opus-5.5-max')   # changed while waiting

    asyncio.run(Assistant.resume(op_instance, suspended))

    assert models == [parse_model('sonnet-5.5-medium')] * 2
    assert op_instance.context_window[0]['reasoning_details'] == [{'type': 'reasoning.text', 'text': 'thinking'}]


def test_the_step_cap_counts_the_steps_before_the_wait(op_instance, monkeypatch):
    run_tools(monkeypatch)
    monkeypatch.setattr(Assistant, 'max_tool_steps', 3)
    llm = FakeLLM([tool_calls_response(tool_call('a', name='list_reminders')), tool_calls_response(bash_call())], default=tool_calls_response(tool_call('x', name='list_reminders')))
    original = llm.create

    async def create(messages, model, tools=None, chat_id=None, tool_choice=None):
        response = await original(messages, model, tools, chat_id, tool_choice)
        return final_response('forced') if tool_choice == 'none' else response

    monkeypatch.setattr(Assistant.ChatCompletion, 'create', create)
    suspended = asyncio.run(Assistant.get_response(op_instance))
    assert suspended.steps_left == 1

    result = asyncio.run(Assistant.resume(op_instance, suspended))

    assert result == 'forced'
    assert len(llm.calls) == 4   # 2 before the wait, then 1 more step and the forced answer


def test_a_resumed_step_can_ask_again(op_instance, monkeypatch):
    run_tools(monkeypatch)
    llm = FakeLLM([tool_calls_response(bash_call('a', 'first')), tool_calls_response(bash_call('b', 'second')), final_response('end')])
    monkeypatch.setattr(Assistant.ChatCompletion, 'create', llm.create)
    first = asyncio.run(Assistant.get_response(op_instance))

    second = asyncio.run(Assistant.resume(op_instance, first))

    assert isinstance(second, Assistant.Suspended)
    assert second.calls[0][1]['command'] == 'second'
    assert asyncio.run(Assistant.resume(op_instance, second)) == 'end'


def test_messages_added_during_the_wait_stay_before_the_tool_round(op_instance, monkeypatch):
    run_tools(monkeypatch)
    monkeypatch.setattr(Assistant.ChatCompletion, 'create', FakeLLM([tool_calls_response(bash_call()), final_response('done')]).create)
    op_instance.add_user_message('first request')
    suspended = asyncio.run(Assistant.get_response(op_instance))
    op_instance.add_user_message('something else meanwhile')

    asyncio.run(Assistant.resume(op_instance, suspended))

    assert roles(op_instance) == ['user', 'user', 'assistant', 'tool', 'assistant']


def test_resume_on_a_chat_over_its_cap_runs_nothing(op_instance, monkeypatch):
    ran = run_tools(monkeypatch)
    monkeypatch.setattr(Assistant.ChatCompletion, 'create', FakeLLM([tool_calls_response(bash_call())]).create)
    suspended = asyncio.run(Assistant.get_response(op_instance))

    async def over_cap(chat_id):
        raise Assistant.Usage.CapReached()

    monkeypatch.setattr(Assistant.Usage, 'check', over_cap)

    assert asyncio.run(Assistant.resume(op_instance, suspended)) == Assistant.CAP_MESSAGE
    assert ran == []
    with pytest.raises(Assistant.Usage.CapReached):
        asyncio.run(Assistant.resume(op_instance, suspended, raise_errors=True))


def test_an_api_error_after_resuming_gives_a_message_and_keeps_the_tool_round(op_instance, monkeypatch):
    run_tools(monkeypatch)
    llm = FakeLLM([tool_calls_response(bash_call())])
    monkeypatch.setattr(Assistant.ChatCompletion, 'create', llm.create)
    suspended = asyncio.run(Assistant.get_response(op_instance))

    async def broken(messages, model, tools=None, chat_id=None, tool_choice=None):
        raise openai.APIConnectionError(request=httpx.Request('POST', 'https://openrouter.ai/api/v1/chat/completions'))

    monkeypatch.setattr(Assistant.ChatCompletion, 'create', broken)

    assert asyncio.run(Assistant.resume(op_instance, suspended)) == Assistant.PROVIDER_MESSAGE
    assert roles(op_instance) == ['assistant', 'tool']
