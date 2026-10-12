import asyncio
import os
import time
from types import SimpleNamespace

import pytest

import access as Access
import assistant as Assistant
from tools import native
from tools.native import registry
from tools.registry import ToolContext

OP_CHAT = 7


@pytest.fixture(autouse=True)
def op_chat(monkeypatch):
    monkeypatch.setattr(Access, '_op_chats', frozenset({OP_CHAT}))


def call(command, chat_id=OP_CHAT, cwd=None, timeout_seconds=None, keep=None):
    arguments = {'command': command, 'cwd': cwd, 'timeout_seconds': timeout_seconds, 'keep': keep}
    return asyncio.run(registry.call('bash', arguments, ToolContext(instance=SimpleNamespace(unique_id=chat_id), client=None)))


def test_it_does_nothing_in_a_chat_that_is_not_enabled(tmp_path):
    marker = tmp_path / 'ran'

    result = call(f'touch {marker}', chat_id=8)

    assert result == {'ok': False, 'error': 'The bash tool is not enabled in this chat.'}
    assert not marker.exists()


def test_it_is_off_everywhere_when_no_chat_is_enabled(monkeypatch):
    monkeypatch.setattr(Access, '_op_chats', frozenset())

    assert call('echo hi')['ok'] is False


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


def test_it_stops_its_own_command_before_the_loop_gives_up_on_it():
    assert native.BASH_TIMEOUT_S <= native.BASH_MAX_TIMEOUT_S < native.BASH_LOOP_TIMEOUT_S
    assert registry.timeout('bash') == native.BASH_LOOP_TIMEOUT_S
    assert registry.timeout('set_reminder') is None


def test_the_default_limit_is_not_longer_than_the_loops_normal_one():
    assert native.BASH_TIMEOUT_S < Assistant.tool_timeout


# ---- end of the output (X3)

def test_keep_end_returns_the_last_part_of_a_long_output(monkeypatch):
    monkeypatch.setattr(native, 'BASH_MAX_OUTPUT', 10)

    result = call('printf 0123456789abcdefghij', keep='end')

    assert result['output'] == 'abcdefghij'
    assert result['truncated'] is True and result['omitted_characters'] == 10


def test_keep_start_and_null_return_the_first_part(monkeypatch):
    monkeypatch.setattr(native, 'BASH_MAX_OUTPUT', 10)

    assert call('printf 0123456789abcdefghij', keep='start')['output'] == '0123456789'
    assert call('printf 0123456789abcdefghij')['output'] == '0123456789'


def test_keep_end_changes_nothing_when_the_output_fits():
    assert call('echo hi', keep='end') == {'ok': True, 'exit_code': 0, 'output': 'hi\n', 'truncated': False, 'omitted_characters': 0}


def test_keep_rejects_other_values():
    result = call('echo hi', keep='middle')

    assert result['ok'] is False and 'Invalid arguments' in result['error']


# ---- working directory (X1)

@pytest.fixture
def workspace(tmp_path, monkeypatch):
    root = tmp_path / 'home'
    (root / 'project').mkdir(parents=True)
    monkeypatch.setitem(native.config, 'workspace_root', str(root))
    return root


def test_cwd_inside_the_workspace_is_where_the_command_runs(workspace):
    result = call('pwd', cwd=str(workspace / 'project'))

    assert result['ok'] is True and result['output'].strip() == str((workspace / 'project').resolve())


def test_cwd_may_be_the_workspace_itself(workspace):
    assert call('pwd', cwd=str(workspace))['output'].strip() == str(workspace.resolve())


def test_cwd_starting_with_a_tilde_means_the_home_folder(monkeypatch, tmp_path):
    monkeypatch.setenv('HOME', str(tmp_path))
    (tmp_path / 'notes').mkdir()
    monkeypatch.setitem(native.config, 'workspace_root', '~')

    assert call('pwd', cwd='~/notes')['output'].strip() == str((tmp_path / 'notes').resolve())


def test_cwd_outside_the_workspace_is_refused_and_nothing_runs(workspace, tmp_path):
    marker = tmp_path / 'ran'

    result = call(f'touch {marker}', cwd=str(tmp_path))

    assert result['ok'] is False and 'must be inside' in result['error']
    assert not marker.exists()


def test_cwd_cannot_escape_with_dot_dot(workspace):
    result = call('pwd', cwd=str(workspace / 'project' / '..' / '..'))

    assert result['ok'] is False and 'must be inside' in result['error']


def test_cwd_cannot_escape_through_a_symlink(workspace, tmp_path):
    outside = tmp_path / 'outside'
    outside.mkdir()
    (workspace / 'link').symlink_to(outside)

    result = call('pwd', cwd=str(workspace / 'link'))

    assert result['ok'] is False and 'must be inside' in result['error']


def test_a_sibling_folder_with_the_same_prefix_is_not_inside(workspace, tmp_path):
    sibling = tmp_path / 'home-other'
    sibling.mkdir()

    assert call('pwd', cwd=str(sibling))['ok'] is False


def test_cwd_must_be_absolute(workspace):
    result = call('pwd', cwd='project')

    assert result['ok'] is False and 'absolute' in result['error']


def test_cwd_must_exist_and_be_a_folder(workspace):
    (workspace / 'file').write_text('x')

    assert 'not a folder' in call('pwd', cwd=str(workspace / 'missing'))['error']
    assert 'not a folder' in call('pwd', cwd=str(workspace / 'file'))['error']


# ---- timeout (X2)

def test_timeout_seconds_overrides_the_default(monkeypatch):
    monkeypatch.setattr(native, 'BASH_TIMEOUT_S', 30)

    result = call('sleep 30', timeout_seconds=1)

    assert result['ok'] is False and 'stopped after 1 seconds' in result['error']


def test_a_command_longer_than_the_default_runs_when_more_time_is_asked_for(monkeypatch):
    monkeypatch.setattr(native, 'BASH_TIMEOUT_S', 1)

    assert call('sleep 2; echo done', timeout_seconds=5)['output'] == 'done\n'


@pytest.mark.parametrize('seconds', [0, -1, 10_000])
def test_timeout_seconds_outside_the_allowed_range_is_refused_and_nothing_runs(seconds, tmp_path):
    marker = tmp_path / 'ran'

    result = call(f'touch {marker}', timeout_seconds=seconds)

    assert result['ok'] is False and 'between 1 and' in result['error']
    assert not marker.exists()


def test_the_longest_allowed_timeout_comes_from_the_config():
    assert native.BASH_MAX_TIMEOUT_S == native.config['bash_max_timeout_s']


def test_the_description_says_it_has_no_sandbox():
    description = next(s['function']['description'] for s in registry.schemas() if s['type'] == 'function' and s['function']['name'] == 'bash')

    assert 'no sandbox' in description
    assert registry.schemas()[-1]['type'].startswith('openrouter:')
    assert [s['function']['name'] for s in registry.schemas() if s['type'] == 'function'][-1] == 'bash'
