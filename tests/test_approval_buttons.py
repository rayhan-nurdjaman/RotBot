import asyncio
from types import SimpleNamespace

import pytest
import telegram
import telegram.ext

import access as Access
import approvals as Approvals
import client as Client

# The buttons, driven through PTB's own dispatcher with real Update objects. Telegram is never contacted.

USER = 11
CHAT = -100500


@pytest.fixture(autouse=True)
def setup(monkeypatch):
    monkeypatch.setattr(Access, '_allowed_users', frozenset({USER}))
    monkeypatch.setattr(Access, '_op_chats', frozenset({CHAT}))
    monkeypatch.setattr(Approvals, '_pending', {})

    async def fake_initialize(self):
        self._bot_user = telegram.User(id=1, first_name='bot', is_bot=True, username='rotrot_botbot')

    async def nothing(self):
        pass

    async def no_network(self, *args, **kwargs):
        raise AssertionError('This test tried to reach Telegram.')

    monkeypatch.setattr(telegram.ext.ExtBot, 'initialize', fake_initialize)
    monkeypatch.setattr(telegram.ext.ExtBot, 'shutdown', nothing)
    monkeypatch.setattr(telegram.request.HTTPXRequest, 'do_request', no_network)


@pytest.fixture(autouse=True)
def fail_on_dispatch_errors(caplog):
    yield
    errors = [r for r in caplog.get_records('call') if r.name == 'telegram.ext.Application' and r.levelname == 'ERROR']
    assert errors == [], 'PTB logged an error while dispatching: ' + errors[0].getMessage()


@pytest.fixture
def pressed(monkeypatch):
    calls = []

    async def on_approval(chat_id, user, request_id, action):
        calls.append((chat_id, user.id, request_id, action))

    monkeypatch.setattr(Client, 'on_approval_callback', on_approval)
    return calls


@pytest.fixture
def answers(monkeypatch):
    shown = []

    async def answer(self, text=None, *args, **kwargs):
        shown.append(text)
        return True

    monkeypatch.setattr(telegram.CallbackQuery, 'answer', answer)
    return shown


def pending_in(chat_id=CHAT):
    async def never(pending):
        pass

    pending = Approvals.create(chat_id, None, never)
    pending.timer.cancel()
    return pending


def press(data, user_id=USER, chat_id=CHAT):
    chat = telegram.Chat(id=chat_id, type='supergroup')
    message = telegram.Message(message_id=9, date=None, chat=chat, text='approval text')
    query = telegram.CallbackQuery(id='q1', from_user=telegram.User(id=user_id, first_name='x', is_bot=False), chat_instance='c', data=data, message=message)
    update = telegram.Update(update_id=1, callback_query=query)
    update.set_bot(Client.application.bot)
    query.set_bot(Client.application.bot)
    message.set_bot(Client.application.bot)

    async def run():
        await Client.application.initialize()
        try:
            await Client.application.process_update(update)
        finally:
            await Client.application.shutdown()

    asyncio.run(run())


def run_in_loop(coroutine_function):
    async def wrapper():
        return await coroutine_function()
    return asyncio.run(wrapper())


@pytest.mark.parametrize('action', Approvals.ACTIONS)
def test_an_allowed_user_pressing_a_button_reaches_the_callback(action, pressed, answers):
    async def make():
        return pending_in()

    pending = run_in_loop(make)

    press(Approvals.callback_data(pending.id, action))

    assert pressed == [(CHAT, USER, pending.id, action)]
    assert answers == [None]


def test_a_stranger_pressing_a_button_is_dropped_like_any_other_update(pressed, answers, caplog):
    async def make():
        return pending_in()

    pending = run_in_loop(make)

    with caplog.at_level('WARNING', logger='client'):
        press(Approvals.callback_data(pending.id, Approvals.ONCE), user_id=99)

    assert pressed == [] and answers == []
    assert 'user id 99' in caplog.text
    assert Approvals.peek(pending.id) is pending


def test_a_press_on_a_request_that_is_gone_is_told_so_and_does_nothing(pressed, answers):
    press(Approvals.callback_data('gone', Approvals.ONCE))

    assert pressed == []
    assert answers == ['This request is no longer valid.']


def test_a_press_from_another_chat_is_refused(pressed, answers):
    async def make():
        return pending_in(CHAT)

    pending = run_in_loop(make)

    press(Approvals.callback_data(pending.id, Approvals.ONCE), chat_id=-100999)

    assert pressed == []
    assert answers == ['This request is no longer valid.']
    assert Approvals.peek(pending.id) is pending


def test_other_callback_data_never_reaches_the_approval_handler(pressed, answers):
    press('something-else')
    press('ap:x:maybe')

    assert pressed == [] and answers == ['This request is no longer valid.']


def test_the_approval_handler_is_registered_after_the_gate():
    groups = Client.application.handlers
    assert any(isinstance(h, telegram.ext.CallbackQueryHandler) and h.callback is Client.approval_pressed for h in groups[0])
    assert all(not isinstance(h, telegram.ext.CallbackQueryHandler) for h in groups[-1])


# ---- sending and closing the message

class ApprovalBot:
    def __init__(self, fail_edit=False, fail_send=False):
        self.sent, self.edited = [], []
        self.fail_edit, self.fail_send = fail_edit, fail_send

    async def send_message(self, chat_id, text, reply_markup=None, parse_mode=None):
        if self.fail_send:
            raise telegram.error.TelegramError('down')
        self.sent.append((chat_id, text, reply_markup, parse_mode))
        return SimpleNamespace(message_id=77)

    async def edit_message_text(self, chat_id, message_id, text, reply_markup=None):
        if self.fail_edit:
            raise telegram.error.TelegramError('gone')
        self.edited.append((chat_id, message_id, text, reply_markup))


def use_bot(monkeypatch, bot):
    monkeypatch.setattr(Client, 'application', SimpleNamespace(bot=bot))


def test_the_message_has_three_buttons_with_valid_callback_data(monkeypatch):
    bot = ApprovalBot()
    use_bot(monkeypatch, bot)

    message_id = asyncio.run(Client.send_approval_request(CHAT, 'run ls?', 'req123'))

    chat_id, text, markup, parse_mode = bot.sent[0]
    assert message_id == 77 and chat_id == CHAT and text == 'run ls?'
    assert parse_mode is None
    rows = markup.inline_keyboard
    assert [[b.text for b in row] for row in rows] == [['Approve this time', 'Deny'], ['Approve for the day']]
    data = [b.callback_data for row in rows for b in row]
    assert [Approvals.parse_callback_data(d) for d in data] == [('req123', 'once'), ('req123', 'deny'), ('req123', 'day')]
    assert all(len(d.encode()) <= 64 for d in data)


def test_a_message_over_telegrams_limit_is_cut_not_rejected(monkeypatch):
    bot = ApprovalBot()
    use_bot(monkeypatch, bot)

    asyncio.run(Client.send_approval_request(CHAT, 'x' * 10_000, 'r'))

    assert len(bot.sent[0][1]) == Client.MESSAGE_LIMIT


def test_failing_to_send_the_request_propagates_so_the_caller_can_fail_closed(monkeypatch):
    use_bot(monkeypatch, ApprovalBot(fail_send=True))

    with pytest.raises(telegram.error.TelegramError):
        asyncio.run(Client.send_approval_request(CHAT, 'run ls?', 'r'))


def test_closing_a_request_replaces_the_text_and_leaves_no_buttons(monkeypatch):
    bot = ApprovalBot()
    use_bot(monkeypatch, bot)

    asyncio.run(Client.close_approval_request(CHAT, 77, 'Approved by alice'))

    assert bot.edited == [(CHAT, 77, 'Approved by alice', None)]


def test_failing_to_close_a_request_is_only_logged(monkeypatch, caplog):
    use_bot(monkeypatch, ApprovalBot(fail_edit=True))

    asyncio.run(Client.close_approval_request(CHAT, 77, 'Approved'))

    assert 'Could not update the approval message' in caplog.text
