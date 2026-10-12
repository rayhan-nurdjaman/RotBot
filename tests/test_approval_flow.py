import asyncio
import json
import logging
from types import SimpleNamespace

import openai
import pytest
import telegram.error

import access as Access
import approvals as Approvals
import assistant as Assistant
import main

# The whole approval flow through main.py: the real agent loop and the real approvals module. Only the model,
# Telegram and the execution of tools are faked.

CHAT = 'op-chat'
ALICE = SimpleNamespace(id=11, username='alice', first_name='Alice')
BOB = SimpleNamespace(id=12, username=None, first_name='Bob')


def bash_call(call_id='b1', command='ls'):
    arguments = json.dumps({'command': command, 'cwd': None, 'timeout_seconds': None, 'keep': None})
    return SimpleNamespace(id=call_id, function=SimpleNamespace(name='bash', arguments=arguments))


def other_call(call_id='o1', name='list_reminders'):
    return SimpleNamespace(id=call_id, function=SimpleNamespace(name=name, arguments='{}'))


def tools_response(*calls):
    return SimpleNamespace(finish_reason='tool_calls', message=SimpleNamespace(content=None, tool_calls=list(calls)))


def text_response(text):
    return SimpleNamespace(finish_reason='stop', message=SimpleNamespace(content=text, tool_calls=None))


class World:
    def __init__(self, monkeypatch, script):
        self.script = list(script)
        self.llm_requests = []
        self.ran = []
        self.sent = []
        self.requests = []     # (chat_id, text, request_id)
        self.closed = []       # (chat_id, message_id, text)
        self.fail_request = False
        self.fail_send = False
        monkeypatch.setattr(main, 'instances', {})
        monkeypatch.setattr(Access, '_op_chats', frozenset({CHAT}))
        monkeypatch.setattr(Approvals, '_pending', {})
        monkeypatch.setattr(Approvals, '_day_approved', {})
        monkeypatch.setitem(Approvals.config, 'approval_timeout_s', 60)
        monkeypatch.setattr(Assistant.ChatCompletion, 'create', self.create)
        monkeypatch.setattr(Assistant.registry, 'call', self.call_tool)
        monkeypatch.setattr(main.Client, 'send_indicator', self.indicator)
        monkeypatch.setattr(main.Client, 'send_message', self.send_message)
        monkeypatch.setattr(main.Client, 'send_approval_request', self.send_approval_request)
        monkeypatch.setattr(main.Client, 'close_approval_request', self.close_approval_request)

    async def create(self, messages, model, tools=None, chat_id=None, tool_choice=None):
        self.llm_requests.append(list(messages))
        return self.script.pop(0)

    async def call_tool(self, name, arguments, ctx):
        self.ran.append((name, arguments))
        return {'ran': name}

    async def indicator(self, instance, indicator):
        pass

    async def send_message(self, chat_id, message):
        if self.fail_send:
            raise telegram.error.NetworkError('down')
        self.sent.append(message)

    async def send_approval_request(self, chat_id, text, request_id):
        if self.fail_request:
            raise telegram.error.NetworkError('down')
        self.requests.append((chat_id, text, request_id))
        return 100 + len(self.requests)

    async def close_approval_request(self, chat_id, message_id, text):
        self.closed.append((chat_id, message_id, text))

    @property
    def instance(self):
        return main.get_instance(CHAT)

    def roles(self):
        return [m['role'] for m in self.instance.context_window]

    def last_tool_results(self):
        return [json.loads(m['content']) for m in self.instance.context_window if m['role'] == 'tool']


@pytest.fixture
def world(monkeypatch):
    def make(*script):
        return World(monkeypatch, script)
    return make


def user_says(text='alice: run ls'):
    return main.on_message(CHAT, text)


# ---- asking

def test_a_message_that_makes_the_model_call_bash_sends_the_buttons_and_runs_nothing(world):
    w = world(tools_response(bash_call(command='make test')))

    asyncio.run(user_says())

    assert w.ran == [] and w.sent == []
    assert len(w.requests) == 1
    chat_id, text, request_id = w.requests[0]
    assert chat_id == CHAT and '$ make test' in text
    assert Approvals.peek(request_id) is not None
    assert w.roles() == ['user']


def test_the_chat_lock_is_free_while_the_buttons_are_showing(world):
    w = world(tools_response(bash_call()))

    async def go():
        await user_says()
        return w.instance.lock.locked()

    assert asyncio.run(go()) is False


def test_a_chat_without_bash_enabled_is_not_asked(world, monkeypatch):
    w = world(tools_response(bash_call()), text_response('it is not enabled here'))
    monkeypatch.setattr(Access, '_op_chats', frozenset())

    asyncio.run(user_says())

    assert w.requests == []
    assert w.sent == ['it is not enabled here']


# ---- answering

def run_answer(w, action, user=ALICE, then=None):
    async def go():
        await user_says()
        request_id = w.requests[0][2]
        await main.on_approval(CHAT, user, request_id, action)
        if then:
            await then(request_id)

    asyncio.run(go())


def test_approving_once_runs_the_command_and_the_model_answers(world):
    w = world(tools_response(bash_call(command='pwd')), text_response('it printed /home'))

    run_answer(w, Approvals.ONCE)

    assert w.ran == [('bash', {'command': 'pwd', 'cwd': None, 'timeout_seconds': None, 'keep': None})]
    assert w.sent == ['it printed /home']
    assert w.roles() == ['user', 'assistant', 'tool', 'assistant']
    assert w.closed[0][2].endswith('Approved by alice.')
    assert '$ pwd' in w.closed[0][2]
    assert Approvals.count() == 0
    assert Approvals.day_approved(CHAT) is False


def test_denying_runs_nothing_and_tells_the_model(world):
    w = world(tools_response(bash_call()), text_response('ok, I will not'))

    run_answer(w, Approvals.DENY)

    assert w.ran == []
    assert w.sent == ['ok, I will not']
    assert w.last_tool_results() == [{'ok': False, 'error': main.DENIED_REFUSAL}]
    assert w.closed[0][2].endswith('Denied by alice. Nothing was run.')


def test_the_name_falls_back_to_first_name_when_there_is_no_username(world):
    w = world(tools_response(bash_call()), text_response('done'))

    run_answer(w, Approvals.ONCE, user=BOB)

    assert w.closed[0][2].endswith('Approved by Bob.')


def test_approving_for_the_day_runs_this_one_and_all_later_ones_without_asking(world):
    w = world(tools_response(bash_call(command='first')), tools_response(bash_call('b2', 'second')), text_response('both done'))

    run_answer(w, Approvals.DAY)

    assert [call[1]['command'] for call in w.ran] == ['first', 'second']
    assert len(w.requests) == 1
    assert w.sent == ['both done']
    assert 'and everything else in this chat until' in w.closed[0][2]
    assert Approvals.day_approved(CHAT) is True


def test_the_next_message_after_a_day_approval_runs_bash_at_once(world):
    w = world(tools_response(bash_call()), text_response('one'), tools_response(bash_call('b2', 'later')), text_response('two'))

    async def go():
        await user_says()
        await main.on_approval(CHAT, ALICE, w.requests[0][2], Approvals.DAY)
        await user_says('alice: again')

    asyncio.run(go())

    assert len(w.requests) == 1
    assert [call[1]['command'] for call in w.ran] == ['ls', 'later']
    assert w.sent == ['one', 'two']


def test_a_step_with_bash_and_other_tools_runs_none_of_them_until_approved(world):
    w = world(tools_response(other_call('o1'), bash_call('b1'), other_call('o2', 'cancel_reminder')), text_response('all three ran'))

    async def go():
        await user_says()
        assert w.ran == []
        assert '3 calls together' in w.requests[0][1]
        await main.on_approval(CHAT, ALICE, w.requests[0][2], Approvals.ONCE)

    asyncio.run(go())

    assert sorted(name for name, _ in w.ran) == ['bash', 'cancel_reminder', 'list_reminders']


def test_denying_a_mixed_step_refuses_every_call_in_it(world):
    w = world(tools_response(other_call('o1'), bash_call('b1')), text_response('nothing ran'))

    run_answer(w, Approvals.DENY)

    assert w.ran == []
    assert w.last_tool_results() == [{'ok': False, 'error': main.DENIED_REFUSAL}] * 2


# ---- answered once, in the right chat

def test_a_second_press_does_nothing(world):
    w = world(tools_response(bash_call()), text_response('once'))

    async def again(request_id):
        await main.on_approval(CHAT, ALICE, request_id, Approvals.ONCE)
        await main.on_approval(CHAT, ALICE, request_id, Approvals.DENY)

    run_answer(w, Approvals.ONCE, then=again)

    assert len(w.ran) == 1 and w.sent == ['once'] and len(w.closed) == 1


def test_a_press_for_a_request_of_another_chat_does_nothing(world):
    w = world(tools_response(bash_call()))

    async def go():
        await user_says()
        await main.on_approval('some-other-chat', ALICE, w.requests[0][2], Approvals.ONCE)
        return Approvals.peek(w.requests[0][2])

    # The request is taken before the chat is compared, so it is gone either way; nothing ran in either chat.
    asyncio.run(go())
    assert w.ran == [] and w.sent == []


def test_a_press_for_an_unknown_request_does_nothing(world):
    w = world()

    asyncio.run(main.on_approval(CHAT, ALICE, 'never-existed', Approvals.ONCE))

    assert w.ran == [] and w.sent == [] and w.closed == []


# ---- expiry

def test_an_unanswered_request_expires_and_the_model_is_told(world, monkeypatch):
    w = world(tools_response(bash_call()), text_response('you did not answer'))
    monkeypatch.setitem(Approvals.config, 'approval_timeout_s', 0.05)

    async def go():
        await user_says()
        await asyncio.sleep(0.3)

    asyncio.run(go())

    assert w.ran == []
    assert w.sent == ['you did not answer']
    assert w.last_tool_results() == [{'ok': False, 'error': main.EXPIRED_REFUSAL}]
    assert 'Not answered in time' in w.closed[0][2]
    assert Approvals.count() == 0


def test_a_press_after_the_expiry_does_nothing(world, monkeypatch):
    w = world(tools_response(bash_call()), text_response('expired'))
    monkeypatch.setitem(Approvals.config, 'approval_timeout_s', 0.05)

    async def go():
        await user_says()
        request_id = w.requests[0][2]
        await asyncio.sleep(0.3)
        await main.on_approval(CHAT, ALICE, request_id, Approvals.ONCE)

    asyncio.run(go())

    assert w.ran == [] and w.sent == ['expired'] and len(w.closed) == 1


def test_a_press_before_the_expiry_cancels_it(world, monkeypatch):
    w = world(tools_response(bash_call()), text_response('approved'))
    monkeypatch.setitem(Approvals.config, 'approval_timeout_s', 0.2)

    async def go():
        await user_says()
        await main.on_approval(CHAT, ALICE, w.requests[0][2], Approvals.ONCE)
        await asyncio.sleep(0.4)

    asyncio.run(go())

    assert len(w.ran) == 1 and w.sent == ['approved'] and len(w.closed) == 1


# ---- the chat carries on while the buttons are showing

def test_a_message_while_waiting_is_stored_not_answered_and_the_chat_is_told_once(world):
    w = world(tools_response(bash_call()), text_response('saw both'))

    async def go():
        await user_says()
        await user_says('alice: yes')
        await user_says('alice: please')
        assert len(w.llm_requests) == 1
        await main.on_approval(CHAT, ALICE, w.requests[0][2], Approvals.ONCE)

    asyncio.run(go())

    assert w.sent.count(main.WAITING_NOTICE) == 1
    assert w.sent[-1] == 'saw both'
    final_request = [m['role'] for m in w.llm_requests[1] if m['role'] != 'system']
    assert final_request == ['user', 'user', 'user', 'assistant', 'tool']   # the user's messages came before the tool round


def test_a_message_in_another_chat_is_answered_normally_while_one_waits(world):
    w = world(tools_response(bash_call()), text_response('other chat reply'))

    async def go():
        await user_says()
        await main.on_message('other-chat', 'alice: hello')

    asyncio.run(go())

    assert w.sent == ['other chat reply']


def test_a_reminder_that_fires_while_buttons_are_showing_is_delivered_at_once(world):
    w = world(tools_response(bash_call()), text_response('Hey alice, call mom!'))

    async def go():
        await user_says()
        await main.on_reminder({'id': 1, 'chat_id': CHAT, 'description': 'call mom'})

    asyncio.run(go())

    assert w.sent == ['Hey alice, call mom!']
    assert Approvals.count() == 1


def test_a_reminder_turn_that_needs_approval_sends_the_plain_reminder_and_asks(world):
    w = world(tools_response(bash_call(command='notify-send mom')))

    asyncio.run(main.on_reminder({'id': 1, 'chat_id': CHAT, 'description': 'call mom'}))

    assert w.sent == ['Reminder: call mom']
    assert len(w.requests) == 1 and w.ran == []


# ---- failure to ask

def test_if_the_request_cannot_be_sent_the_command_is_refused_and_the_turn_goes_on(world):
    w = world(tools_response(bash_call()), text_response('I could not ask you'))
    w.fail_request = True

    asyncio.run(user_says())

    assert w.ran == []
    assert w.sent == ['I could not ask you']
    assert w.last_tool_results() == [{'ok': False, 'error': main.NOT_ASKED_REFUSAL}]
    assert Approvals.count() == 0


def test_a_failed_reply_after_an_approval_is_logged(world, caplog):
    w = world(tools_response(bash_call()), text_response('done'))

    async def go():
        await user_says()
        w.fail_send = True
        await main.on_approval(CHAT, ALICE, w.requests[0][2], Approvals.ONCE)

    asyncio.run(go())

    assert 'Could not send the reply' in caplog.text


def test_after_an_approval_the_model_can_ask_again_and_gets_a_new_request(world):
    w = world(tools_response(bash_call(command='one')), tools_response(bash_call('b2', 'two')), text_response('finished'))

    async def go():
        await user_says()
        await main.on_approval(CHAT, ALICE, w.requests[0][2], Approvals.ONCE)
        assert len(w.requests) == 2 and w.sent == []
        await main.on_approval(CHAT, ALICE, w.requests[1][2], Approvals.ONCE)

    asyncio.run(go())

    assert [call[1]['command'] for call in w.ran] == ['one', 'two']
    assert w.sent == ['finished']


def test_the_model_being_down_after_an_approval_gives_the_user_a_message(world, monkeypatch):
    w = world(tools_response(bash_call()))

    async def broken(messages, model, tools=None, chat_id=None, tool_choice=None):
        raise openai.APIConnectionError(request=SimpleNamespace(method='POST', url='http://x'))

    async def go():
        await user_says()
        monkeypatch.setattr(Assistant.ChatCompletion, 'create', broken)
        await main.on_approval(CHAT, ALICE, w.requests[0][2], Approvals.ONCE)

    asyncio.run(go())

    assert w.sent == [Assistant.PROVIDER_MESSAGE]
    assert len(w.ran) == 1


def test_an_approval_is_logged_with_the_request_and_user_ids(world, caplog):
    w = world(tools_response(bash_call()), text_response('ok'))

    with caplog.at_level(logging.INFO):
        run_answer(w, Approvals.ONCE)

    assert 'once by user 11' in caplog.text
