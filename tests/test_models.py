import asyncio
from types import SimpleNamespace

import pytest

import assistant as Assistant
import chat_completion as ChatCompletion
import main
import router
from instance import Instance
from models import DEFAULT_MODEL, ModelSpec, parse_model


@pytest.mark.parametrize('text, expected', [
    ('haiku-5.5', ModelSpec('haiku-5.5', 'low')),
    ('haiku-5.5-high', ModelSpec('haiku-5.5', 'high')),
    ('Sonnet-5.5-MEDIUM', ModelSpec('sonnet-5.5', 'medium')),
    ('opus-5.5-xhigh', ModelSpec('opus-5.5', 'xhigh')),
    ('  opus-5.5-max ', ModelSpec('opus-5.5', 'max')),
])
def test_parse_model(text, expected):
    assert parse_model(text) == expected


@pytest.mark.parametrize('text', ['', 'gpt-4o', 'haiku', 'haiku-5.5-ultra', 'haiku-4.5-low', 'low', '-low'])
def test_parse_model_rejects_unknown(text):
    with pytest.raises(ValueError):
        parse_model(text)


def test_default_is_cheapest_model_at_lowest_effort():
    assert str(DEFAULT_MODEL) == 'haiku-5.5-low'
    assert DEFAULT_MODEL.slug == 'anthropic/claude-haiku-5.5'


def test_new_instance_defaults(tmp_path, monkeypatch):
    monkeypatch.setattr('instance.memory_file', str(tmp_path / 'memory.json'))
    instance = Instance('chat-1')
    assert instance.model == DEFAULT_MODEL
    assert instance.auto_route is False


def test_personality_switch_does_not_change_model(tmp_path, monkeypatch):
    monkeypatch.setattr('instance.memory_file', str(tmp_path / 'memory.json'))
    instance = Instance('chat-1')
    instance.model = parse_model('opus-5.5-high')
    instance.set_personality('lockedin')
    assert instance.model == parse_model('opus-5.5-high')


FAKE_CHOICE = SimpleNamespace(
    finish_reason='stop',
    message=SimpleNamespace(content='hi', tool_calls=None),
)


class FakeCompletions:
    def __init__(self):
        self.kwargs = None

    async def create(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(choices=[FAKE_CHOICE], usage=None)


def capture_request(monkeypatch, **call_kwargs):
    completions = FakeCompletions()
    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    monkeypatch.setattr(ChatCompletion, 'openai_client', fake_client)
    result = asyncio.run(ChatCompletion.create([{'role': 'user', 'content': 'hi'}], parse_model('sonnet-5.5-high'), **call_kwargs))
    assert result is FAKE_CHOICE
    return completions.kwargs


def test_request_targets_openrouter_and_has_no_temperature(monkeypatch):
    kwargs = capture_request(monkeypatch, tools=[{'type': 'function'}], chat_id=42)

    assert kwargs['model'] == 'anthropic/claude-sonnet-5.5'
    assert 'temperature' not in kwargs
    assert kwargs['extra_body']['reasoning'] == {'effort': 'high'}
    assert kwargs['extra_body']['session_id'] == '42'
    assert kwargs['user'] == '42'
    assert kwargs['max_tokens'] > 0
    assert kwargs['tools'] == [{'type': 'function'}]
    assert kwargs['parallel_tool_calls'] is True


def test_request_without_tools_or_chat_id_omits_them(monkeypatch):
    kwargs = capture_request(monkeypatch)

    assert 'tools' not in kwargs
    assert 'parallel_tool_calls' not in kwargs
    assert 'user' not in kwargs
    assert 'session_id' not in kwargs['extra_body']


def test_client_points_at_openrouter():
    assert str(ChatCompletion.openai_client.base_url).startswith('https://openrouter.ai/api/v1')


def test_no_openai_key_is_read():
    import inspect
    assert 'OPENAI_API_KEY' not in inspect.getsource(ChatCompletion)


def make_instance(tmp_path, monkeypatch):
    monkeypatch.setattr('instance.memory_file', str(tmp_path / 'memory.json'))
    return Instance('chat-1')


def run_turn(instance, monkeypatch):
    used = []

    async def create(messages, model, tools=None, chat_id=None, tool_choice=None):
        used.append(model)
        return SimpleNamespace(finish_reason='stop', message=SimpleNamespace(content='ok', tool_calls=None))

    monkeypatch.setattr(Assistant.ChatCompletion, 'create', create)
    asyncio.run(Assistant.get_response(instance))
    return used


def test_turn_uses_the_chat_model_when_auto_route_is_off(tmp_path, monkeypatch):
    instance = make_instance(tmp_path, monkeypatch)
    instance.model = parse_model('opus-5.5-max')
    monkeypatch.setattr(router, 'route', lambda i: pytest.fail('router must not be used'))

    assert run_turn(instance, monkeypatch) == [parse_model('opus-5.5-max')]


def test_auto_route_overrides_the_chat_model(tmp_path, monkeypatch):
    instance = make_instance(tmp_path, monkeypatch)
    instance.model = parse_model('opus-5.5-max')
    instance.auto_route = True
    monkeypatch.setattr(router, 'route', lambda i: parse_model('sonnet-5.5-medium'))

    assert run_turn(instance, monkeypatch) == [parse_model('sonnet-5.5-medium')]


def test_stub_router_returns_the_cheapest_model(tmp_path, monkeypatch):
    assert router.route(make_instance(tmp_path, monkeypatch)) == DEFAULT_MODEL


def test_model_is_chosen_once_per_tool_loop(tmp_path, monkeypatch):
    instance = make_instance(tmp_path, monkeypatch)
    instance.auto_route = True
    picks = iter([parse_model('haiku-5.5-low'), parse_model('opus-5.5-max')])
    monkeypatch.setattr(router, 'route', lambda i: next(picks))
    used = []
    tool_call = SimpleNamespace(id='a', function=SimpleNamespace(name='get_time', arguments='{}'))
    responses = iter([
        SimpleNamespace(finish_reason='tool_calls', message=SimpleNamespace(content=None, tool_calls=[tool_call])),
        SimpleNamespace(finish_reason='stop', message=SimpleNamespace(content='ok', tool_calls=None)),
    ])

    async def create(messages, model, tools=None, chat_id=None, tool_choice=None):
        used.append(model)
        return next(responses)

    async def tool(name, arguments, ctx):
        return {}

    monkeypatch.setattr(Assistant.ChatCompletion, 'create', create)
    monkeypatch.setattr(Assistant.registry, 'call', tool)
    asyncio.run(Assistant.get_response(instance))

    assert used == [parse_model('haiku-5.5-low')] * 2


def collect_messages(monkeypatch):
    sent = []

    async def send_message(chat_id, message):
        sent.append(message)

    monkeypatch.setattr(main.Client, 'send_message', send_message)
    monkeypatch.setattr(main, 'instances', {})
    return sent


def test_model_command_sets_model_and_effort(monkeypatch):
    sent = collect_messages(monkeypatch)

    asyncio.run(main.set_instance_model('chat-1', 'haiku-5.5-high'))

    assert main.instances['chat-1'].model == parse_model('haiku-5.5-high')
    assert sent == ["_Model set to 'haiku-5.5-high'._"]


def test_model_command_without_effort_defaults_to_cheapest_effort(monkeypatch):
    collect_messages(monkeypatch)
    asyncio.run(main.set_instance_model('chat-1', 'opus-5.5'))
    assert str(main.instances['chat-1'].model) == 'opus-5.5-low'


def test_model_command_without_argument_reports_current(monkeypatch):
    sent = collect_messages(monkeypatch)
    asyncio.run(main.set_instance_model('chat-1', ''))
    assert sent == ["_Current model: 'haiku-5.5-low'_"]


def test_model_command_rejects_unknown_and_keeps_current(monkeypatch):
    sent = collect_messages(monkeypatch)
    asyncio.run(main.set_instance_model('chat-1', 'gpt-4o'))
    assert main.instances['chat-1'].model == DEFAULT_MODEL
    assert 'Unknown model' in sent[0]


def test_autoroute_command_toggles_and_warns_on_model(monkeypatch):
    sent = collect_messages(monkeypatch)

    asyncio.run(main.toggle_auto_route('chat-1'))
    assert main.instances['chat-1'].auto_route is True
    asyncio.run(main.set_instance_model('chat-1', 'sonnet-5.5'))
    assert 'overrides this' in sent[-1]
    asyncio.run(main.toggle_auto_route('chat-1'))
    assert main.instances['chat-1'].auto_route is False
    assert "using 'sonnet-5.5-low'" in sent[-1]


def capture_last_roles(monkeypatch):
    roles = []

    async def create(messages, model, tools=None, chat_id=None, tool_choice=None):
        roles.append(messages[-1]['role'])
        return SimpleNamespace(finish_reason='stop', message=SimpleNamespace(content='ok', tool_calls=None))

    async def send_message(chat_id, message):
        pass

    async def send_indicator(instance, indicator):
        pass

    monkeypatch.setattr(Assistant.ChatCompletion, 'create', create)
    monkeypatch.setattr(main.Client, 'send_message', send_message)
    monkeypatch.setattr(main.Client, 'send_indicator', send_indicator)
    monkeypatch.setattr(main, 'instances', {})
    return roles


def test_requests_never_end_with_an_assistant_turn_on_message(monkeypatch):
    roles = capture_last_roles(monkeypatch)
    asyncio.run(main.on_message('chat-1', 'alice: hi'))
    asyncio.run(main.on_message('chat-1', 'alice: and again'))
    assert roles == ['user', 'user']


def test_requests_never_end_with_an_assistant_turn_on_reminder(monkeypatch):
    roles = capture_last_roles(monkeypatch)
    asyncio.run(main.on_reminder({'id': 1, 'chat_id': 'chat-1', 'description': 'call mom'}))
    assert roles == ['user']


def test_requests_never_end_with_an_assistant_turn_after_a_tool_round_or_forced_final(tmp_path, monkeypatch):
    instance = make_instance(tmp_path, monkeypatch)
    instance.add_user_message('alice: hi')
    tool_call = SimpleNamespace(id='a', function=SimpleNamespace(name='get_time', arguments='{}'))
    roles = []

    async def create(messages, model, tools=None, chat_id=None, tool_choice=None):
        roles.append(messages[-1]['role'])
        return SimpleNamespace(finish_reason='tool_calls', message=SimpleNamespace(content=None, tool_calls=[tool_call]))

    async def tool(name, arguments, ctx):
        return {}

    monkeypatch.setattr(Assistant.ChatCompletion, 'create', create)
    monkeypatch.setattr(Assistant.registry, 'call', tool)
    asyncio.run(Assistant.get_response(instance))

    assert roles[0] == 'user'
    assert set(roles[1:]) == {'tool'}
    assert len(roles) == Assistant.max_tool_steps + 1
