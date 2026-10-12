import asyncio
import os
import time

import assistant as Assistant
from tools import native
from tools.native import registry
from tools.registry import ToolContext


def call(command):
    return asyncio.run(registry.call('bash', {'command': command}, ToolContext(instance=None, client=None)))


def test_it_returns_the_output_and_exit_code():
    assert call('echo hello') == {'ok': True, 'exit_code': 0, 'output': 'hello\n', 'truncated': False, 'omitted_characters': 0}


def test_a_failing_command_is_a_successful_call_with_its_exit_code():
    result = call('echo oops >&2; exit 3')

    assert result['ok'] is True
    assert result['exit_code'] == 3
    assert result['output'] == 'oops\n'


def test_stdout_and_stderr_come_back_together():
    assert call('echo out; echo err >&2')['output'] == 'out\nerr\n'


def test_it_runs_in_the_working_directory_of_the_bot():
    assert call('pwd')['output'].strip() == os.getcwd()


def test_there_is_no_input_to_read():
    started = time.monotonic()

    result = call('cat')

    assert result['output'] == '' and result['exit_code'] == 0
    assert time.monotonic() - started < 5


def test_long_output_is_cut_and_says_how_much_was_left_out(monkeypatch):
    monkeypatch.setattr(native, 'BASH_MAX_OUTPUT', 100)

    result = call('head -c 250 /dev/zero | tr "\\0" x')

    assert len(result['output']) == 100
    assert result['truncated'] is True and result['omitted_characters'] == 150


def test_binary_output_does_not_crash():
    assert call('printf "\\xff\\xfe ok"')['ok'] is True


def test_a_command_that_runs_too_long_is_stopped_with_its_children(monkeypatch, tmp_path):
    monkeypatch.setattr(native, 'BASH_TIMEOUT_S', 1)
    marker = tmp_path / 'child_survived'
    started = time.monotonic()

    result = call(f'(sleep 3; touch {marker}) & sleep 30')

    assert result['ok'] is False and 'stopped after 1 seconds' in result['error']
    assert time.monotonic() - started < 10
    time.sleep(3.5)
    assert not marker.exists()


def test_it_stops_its_own_command_before_the_general_tool_timeout():
    assert native.BASH_TIMEOUT_S < Assistant.tool_timeout


def test_the_description_says_it_has_no_sandbox():
    description = next(s['function']['description'] for s in registry.schemas() if s['type'] == 'function' and s['function']['name'] == 'bash')

    assert 'no sandbox' in description
    assert registry.schemas()[-1]['type'].startswith('openrouter:')
    assert [s['function']['name'] for s in registry.schemas() if s['type'] == 'function'][-1] == 'bash'
