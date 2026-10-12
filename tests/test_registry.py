import asyncio
from types import SimpleNamespace
from typing import Annotated

import pytest
from pydantic import Field

from tools.registry import Registry, ToolContext, ToolError


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def registry():
    return Registry()


@pytest.fixture
def ctx():
    return ToolContext(instance=SimpleNamespace(unique_id=42), client=SimpleNamespace())


def test_schema_matches_the_hand_written_one(registry):
    @registry.tool
    async def save_to_memory(ctx, user_info: Annotated[str, Field(description='The information to store, provided as a complete sentence.')]):
        """Stores user-provided information to enhance future responses and interactions."""
        return {}

    # The schema that config/tools.json held before the registry replaced it.
    assert registry.schemas() == [{
        'type': 'function',
        'function': {
            'name': 'save_to_memory',
            'description': 'Stores user-provided information to enhance future responses and interactions.',
            'strict': True,
            'parameters': {
                'type': 'object',
                'properties': {
                    'user_info': {'type': 'string', 'description': 'The information to store, provided as a complete sentence.'},
                },
                'additionalProperties': False,
                'required': ['user_info'],
            },
        },
    }]


def test_schema_is_strict_with_every_property_required(registry):
    @registry.tool
    async def sample(ctx, a: int, b: str, c: float):
        """Sample."""
        return {}

    function = registry.schemas()[0]['function']
    parameters = function['parameters']
    assert function['strict'] is True
    assert parameters['additionalProperties'] is False
    assert parameters['required'] == ['a', 'b', 'c']
    assert [p['type'] for p in parameters['properties'].values()] == ['integer', 'string', 'number']
    assert 'title' not in parameters and all('title' not in p for p in parameters['properties'].values())


def test_tool_without_arguments_has_empty_required(registry):
    @registry.tool
    async def get_time(ctx):
        """Time."""
        return {}

    parameters = registry.schemas()[0]['function']['parameters']
    assert parameters == {'type': 'object', 'properties': {}, 'required': [], 'additionalProperties': False}


def test_success_is_wrapped_and_receives_the_context(registry, ctx):
    @registry.tool
    async def echo(ctx, text: str):
        """Echo."""
        return {'text': text, 'chat': ctx.chat_id}

    assert run(registry.call('echo', {'text': 'hi'}, ctx)) == {'ok': True, 'text': 'hi', 'chat': 42}


def test_tool_error_becomes_a_failed_result(registry, ctx):
    @registry.tool
    async def fail(ctx):
        """Fail."""
        raise ToolError('nope')

    assert run(registry.call('fail', {}, ctx)) == {'ok': False, 'error': 'nope'}


@pytest.mark.parametrize('arguments', [{}, {'n': 'abc'}, {'n': 1, 'extra': 2}, {'n': None}])
def test_invalid_arguments_are_returned_not_raised(registry, ctx, arguments):
    called = []

    @registry.tool
    async def sample(ctx, n: int):
        """Sample."""
        called.append(n)
        return {}

    result = run(registry.call('sample', arguments, ctx))
    assert result['ok'] is False and result['error'].startswith('Invalid arguments:')
    assert called == []


def test_unknown_tool_is_returned_not_raised(registry, ctx):
    assert run(registry.call('missing', {}, ctx)) == {'ok': False, 'error': 'Unknown tool \'missing\'.'}


def test_unexpected_exceptions_propagate_to_the_caller(registry, ctx):
    @registry.tool
    async def boom(ctx):
        """Boom."""
        raise RuntimeError('bug')

    with pytest.raises(RuntimeError):
        run(registry.call('boom', {}, ctx))


def test_registration_rules(registry):
    async def no_doc(ctx):
        return {}

    async def has_default(ctx, n: int = 1):
        """Doc."""
        return {}

    async def no_hint(ctx, n):
        """Doc."""
        return {}

    for fn in (no_doc, has_default, no_hint):
        with pytest.raises(ValueError):
            registry.tool(fn)

    @registry.tool
    async def once(ctx):
        """Doc."""
        return {}

    with pytest.raises(ValueError):
        registry.tool(once)


def test_native_function_tools_are_strict_and_have_no_idle_message():
    from tools.native import registry as native_registry

    functions = [s['function'] for s in native_registry.schemas() if s['type'] == 'function']
    for function in functions:
        parameters = function['parameters']
        assert function['strict'] is True and parameters['additionalProperties'] is False
        assert parameters['required'] == list(parameters['properties'])
        assert 'idle_message' not in parameters['properties']
    assert [f['name'] for f in functions] == ['save_memory', 'recall_memory', 'forget_memory', 'set_reminder', 'list_reminders', 'cancel_reminder', 'bash']


def test_server_tools_come_after_function_tools_and_are_capped():
    from tools.native import registry as native_registry

    schemas = native_registry.schemas()
    assert [s['type'] for s in schemas] == ['function'] * 7 + ['openrouter:web_search', 'openrouter:web_fetch']
    search, fetch = schemas[7:]
    assert search['parameters']['max_uses'] and search['parameters']['max_results'] and search['parameters']['max_characters']
    assert fetch['parameters']['max_uses'] and fetch['parameters']['max_content_tokens']


def test_server_tool_schema_and_prompt_line(registry):
    registry.server_tool('openrouter:datetime', 'Gets the time.', timezone='Asia/Singapore')
    registry.server_tool('openrouter:web_fetch', 'Fetches.')

    assert registry.schemas() == [
        {'type': 'openrouter:datetime', 'parameters': {'timezone': 'Asia/Singapore'}},
        {'type': 'openrouter:web_fetch'},
    ]
    assert registry.prompt_section() == '1. datetime\n- Gets the time.\n2. web_fetch\n- Fetches.'


def test_server_tools_have_no_handler_so_calling_one_is_an_unknown_tool(registry, ctx):
    registry.server_tool('openrouter:web_search', 'Searches.')

    assert run(registry.call('web_search', {}, ctx))['ok'] is False


def test_server_tool_names_cannot_collide(registry):
    registry.server_tool('openrouter:web_search', 'Searches.')

    with pytest.raises(ValueError):
        registry.server_tool('openrouter:web_search', 'Again.')


def test_prompt_section_is_generated_from_the_registry(registry):
    @registry.tool
    async def first(ctx):
        """Does one thing.
        And another."""
        return {}

    @registry.tool
    async def second(ctx):
        """Second."""
        return {}

    assert registry.prompt_section() == '1. first\n- Does one thing.\n- And another.\n2. second\n- Second.'


def test_system_prompt_lists_every_registered_tool():
    from instance import Instance
    from tools.native import registry as native_registry

    prompt = Instance('chat-1').get_system_prompt()[0]['content'][0]['text']

    assert '{tools}' not in prompt
    assert native_registry.prompt_section() in prompt
    for name in ('save_memory', 'recall_memory', 'forget_memory', 'set_reminder', 'list_reminders', 'cancel_reminder', 'bash', 'web_search', 'web_fetch'):
        assert name in prompt
