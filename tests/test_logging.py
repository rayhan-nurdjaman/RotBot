import asyncio
import json
import logging
from types import SimpleNamespace

import chat_completion as ChatCompletion
from instance import Instance
from models import parse_model

USAGE = SimpleNamespace(
    prompt_tokens=120,
    completion_tokens=40,
    completion_tokens_details=SimpleNamespace(reasoning_tokens=25),
    prompt_tokens_details=SimpleNamespace(cached_tokens=100),
    cost=0.00123,
)


def tool_call(name):
    return SimpleNamespace(id='x', function=SimpleNamespace(name=name, arguments='{}'))


def call_with(monkeypatch, caplog, usage, tool_calls=None, finish_reason='stop'):
    choice = SimpleNamespace(finish_reason=finish_reason, message=SimpleNamespace(content='hi', tool_calls=tool_calls))

    async def create(**kwargs):
        return SimpleNamespace(choices=[choice], usage=usage)

    monkeypatch.setattr(ChatCompletion, 'openai_client', SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))))
    with caplog.at_level(logging.INFO, logger='chat_completion'):
        asyncio.run(ChatCompletion.create([{'role': 'user', 'content': 'hi'}], parse_model('sonnet-5.5-high'), chat_id=42))
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith('llm_call ')]
    assert len(lines) == 1
    return json.loads(lines[0][len('llm_call '):])


def test_one_structured_line_per_call_with_all_fields(monkeypatch, caplog):
    logged = call_with(monkeypatch, caplog, USAGE, tool_calls=[tool_call('get_time'), tool_call('web_search')], finish_reason='tool_calls')

    assert logged['chat_id'] == 42
    assert logged['model'] == 'anthropic/claude-sonnet-5.5'
    assert logged['effort'] == 'high'
    assert logged['prompt_tokens'] == 120
    assert logged['completion_tokens'] == 40
    assert logged['reasoning_tokens'] == 25
    assert logged['cached_tokens'] == 100
    assert logged['cost'] == 0.00123
    assert logged['latency_s'] >= 0
    assert logged['tool_calls'] == ['get_time', 'web_search']
    assert logged['finish_reason'] == 'tool_calls'


def test_missing_usage_is_logged_as_null_not_a_crash(monkeypatch, caplog):
    logged = call_with(monkeypatch, caplog, usage=None)

    assert logged['prompt_tokens'] is None
    assert logged['cost'] is None
    assert logged['tool_calls'] == []


def test_logging_an_image_only_message_does_not_crash(tmp_path, monkeypatch, caplog):
    instance = Instance('chat-1')

    with caplog.at_level(logging.INFO, logger='instance'):
        instance.add_user_message('', 'data:image/jpeg;base64,AAAA')
        instance.add_assistant_message('nice picture')

    assert len(instance.context_window) == 2


TOKEN = '7521151946:AAEUIRahSYxLa7HW2fgZj_l267UadHHIs8x'


def format_with(formatter, message, args=(), exc_info=None):
    record = logging.LogRecord('httpx', logging.INFO, __file__, 1, message, args, exc_info)
    return formatter.format(record)


def test_the_telegram_token_is_redacted_from_a_request_line():
    import logsafe
    formatter = logsafe.RedactingFormatter('%(name)s - %(message)s')

    line = format_with(formatter, 'HTTP Request: %s "%s"', (f'POST https://api.telegram.org/bot{TOKEN}/sendMessage', 'HTTP/1.1 200 OK'))

    assert TOKEN not in line
    assert 'AAEUIRahSYx' not in line
    assert line == 'httpx - HTTP Request: POST https://api.telegram.org/bot<redacted>/sendMessage "HTTP/1.1 200 OK"'


def test_the_telegram_token_is_redacted_from_a_traceback():
    import logsafe
    formatter = logsafe.RedactingFormatter('%(message)s')
    try:
        raise RuntimeError(f'failed for https://api.telegram.org/bot{TOKEN}/getUpdates')
    except RuntimeError:
        import sys
        text = format_with(formatter, 'polling failed', exc_info=sys.exc_info())

    assert TOKEN not in text
    assert 'bot<redacted>/getUpdates' in text


def test_ordinary_log_text_is_not_changed():
    import logsafe
    formatter = logsafe.RedactingFormatter('%(message)s')
    text = 'llm_call {"chat_id": 1928665921, "cost": 0.0007} at 08:53:12, ratio 3:1, user alice: hi'

    assert format_with(formatter, text) == text


def test_the_programs_own_log_handlers_redact_the_token():
    import logsafe
    import main

    assert len(main.log_handlers) == 2
    for handler in main.log_handlers:
        assert isinstance(handler.formatter, logsafe.RedactingFormatter)
        assert TOKEN not in format_with(handler.formatter, f'POST https://api.telegram.org/bot{TOKEN}/sendMessage')
