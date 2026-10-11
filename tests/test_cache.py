import asyncio
import json
from types import SimpleNamespace

import pytest

import assistant as Assistant
import chat_completion as ChatCompletion
from instance import Instance
from models import parse_model


@pytest.fixture
def instance(tmp_path, monkeypatch):
    monkeypatch.setattr('instance.memory_file', str(tmp_path / 'memory.json'))
    return Instance('chat-1')


def breakpoints(messages):
    found = []
    for index, message in enumerate(messages):
        content = message.get('content')
        if isinstance(content, list):
            found += [index for part in content if 'cache_control' in part]
    return found


def test_request_order_is_system_then_memory_then_history(instance):
    instance.add_memory('alice likes tea')
    instance.add_user_message('alice: hi')
    instance.add_assistant_message('hello')
    instance.add_user_message('alice: again')

    roles = [m['role'] for m in instance.get_context()]

    assert roles == ['system', 'user', 'user', 'assistant', 'user']
    assert 'alice likes tea' in instance.get_context()[1]['content'][0]['text']
    assert 'alice likes tea' not in instance.get_context()[0]['content'][0]['text']


def test_exactly_one_breakpoint_on_the_last_message_of_the_request(instance):
    instance.add_memory('alice likes tea')
    instance.add_user_message('alice: hi')
    instance.add_assistant_message('hello')
    instance.add_user_message('alice: again')

    context = instance.get_context()

    assert breakpoints(context) == [len(context) - 1]


def test_breakpoints_never_leak_into_the_stored_context(instance):
    instance.add_user_message('alice: hi')
    for _ in range(3):
        instance.get_context()

    assert 'cache_control' not in json.dumps(instance.context_window)


def test_breakpoint_moves_forward_as_the_history_grows(instance):
    instance.add_user_message('alice: one')
    first = breakpoints(instance.get_context())
    instance.add_assistant_message('reply')
    instance.add_user_message('alice: two')
    second = breakpoints(instance.get_context())

    assert first == [1] and second == [3]


def test_breakpoint_works_on_tool_messages_with_string_content(instance):
    instance.add_user_message('alice: time?')
    instance.add_tool_round([('a', 'get_time', {}, {'time': 'now'})])

    last = instance.get_context()[-1]

    assert last['role'] == 'tool'
    assert last['content'] == [{'type': 'text', 'text': '{"time": "now"}', 'cache_control': {'type': 'ephemeral'}}]
    assert instance.context_window[-1]['content'] == '{"time": "now"}'


def test_breakpoint_on_an_image_message_goes_on_its_last_part(instance):
    instance.add_user_message('alice: what is this?', 'data:image/jpeg;base64,AAAA')

    last = instance.get_context()[-1]

    assert 'cache_control' not in last['content'][0]
    assert last['content'][1]['cache_control'] == {'type': 'ephemeral'}


def test_empty_history_adds_no_breakpoint(instance):
    assert breakpoints(instance.get_context()) == []


def test_assistant_message_with_no_content_is_left_alone(instance):
    instance.add_tool_round([('a', 'get_time', {}, {})])
    assert instance.context_window[0]['role'] == 'assistant'
    # The last message is the tool result here; the assistant tool_calls message is never given a breakpoint.
    assert 'cache_control' not in json.dumps(instance.context_window[0])


def test_system_prompt_is_identical_between_requests_and_after_memory_changes(instance):
    first = instance.get_system_prompt()
    instance.add_memory('alice likes tea')
    instance.add_user_message('alice: hi')

    assert instance.get_system_prompt() == first


def test_history_prefix_is_identical_between_consecutive_requests(instance):
    instance.add_user_message('alice: one')
    instance.add_assistant_message('reply one')
    before = json.dumps(instance.get_context()[:-1] and instance.context_window)
    instance.add_user_message('alice: two')

    after_window = json.dumps(instance.context_window[:2])
    assert json.loads(before)[:2] == json.loads(after_window)


def test_tool_list_is_the_same_on_every_step_of_a_turn(instance, monkeypatch):
    seen = []
    responses = iter([
        SimpleNamespace(finish_reason='tool_calls', message=SimpleNamespace(content='', tool_calls=[
            SimpleNamespace(id='a', function=SimpleNamespace(name='get_time', arguments='{}'))], model_extra={})),
        SimpleNamespace(finish_reason='stop', message=SimpleNamespace(content='done', tool_calls=None, model_extra={})),
    ])

    async def create(messages, model, tools=None, chat_id=None, tool_choice=None):
        seen.append(json.dumps(tools))
        return next(responses)

    async def tool(name, arguments, ctx):
        return {}

    monkeypatch.setattr(Assistant.ChatCompletion, 'create', create)
    monkeypatch.setattr(Assistant.registry, 'call', tool)
    instance.add_user_message('alice: hi')
    asyncio.run(Assistant.get_response(instance))

    assert len(set(seen)) == 1 and seen[0] != 'null'


def test_every_request_sets_the_session_and_user_for_sticky_routing(monkeypatch):
    kwargs_seen = {}

    async def create(**kwargs):
        kwargs_seen.update(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(finish_reason='stop', message=SimpleNamespace(content='x', tool_calls=None))], usage=None)

    monkeypatch.setattr(ChatCompletion, 'openai_client', SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))))
    asyncio.run(ChatCompletion.create([{'role': 'user', 'content': 'hi'}], parse_model('haiku-5.5-low'), chat_id=99))

    assert kwargs_seen['extra_body']['session_id'] == '99'
    assert kwargs_seen['user'] == '99'


def test_tool_choice_none_is_sent_only_when_asked_for(monkeypatch):
    seen = []

    async def create(**kwargs):
        seen.append(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(finish_reason='stop', message=SimpleNamespace(content='x', tool_calls=None))], usage=None)

    monkeypatch.setattr(ChatCompletion, 'openai_client', SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))))
    tools = [{'type': 'function'}]
    asyncio.run(ChatCompletion.create([], parse_model('haiku-5.5-low'), tools))
    asyncio.run(ChatCompletion.create([], parse_model('haiku-5.5-low'), tools, tool_choice='none'))

    assert 'tool_choice' not in seen[0]
    assert seen[1]['tool_choice'] == 'none'
