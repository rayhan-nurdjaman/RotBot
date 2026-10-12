import asyncio

import pytest
import telegram
import telegram.ext
import telegram.request

import access as Access
import client as Client
import main


@pytest.fixture(autouse=True)
def reset_access(monkeypatch):
    monkeypatch.setattr(Access, '_allowed_users', frozenset())
    monkeypatch.setattr(Access, '_op_chats', frozenset())


def load(users, ops=''):
    Access.load({'ALLOWED_USER_IDS': users, 'OP_CHAT_IDS': ops})


# ---- the lists

def test_nobody_is_allowed_before_the_lists_are_loaded():
    assert Access.is_allowed(1) is False
    assert Access.is_op_chat(1) is False


def test_ids_are_read_with_spaces_and_trailing_commas():
    load(' 11 , 22,', ' 11, -100123 ,')

    assert Access.is_allowed(11) and Access.is_allowed(22) and not Access.is_allowed(33)
    assert Access.is_op_chat(11) and Access.is_op_chat(-100123) and not Access.is_op_chat(22)


def test_an_empty_or_missing_allow_list_is_an_error():
    for value in ('', '  ', ',', None):
        with pytest.raises(Access.AccessConfigError, match='ALLOWED_USER_IDS is empty'):
            Access.load({'ALLOWED_USER_IDS': value})


def test_a_username_is_rejected_with_a_clear_message():
    with pytest.raises(Access.AccessConfigError, match="'alice'"):
        load('11,alice')
    with pytest.raises(Access.AccessConfigError, match='Usernames do not work'):
        load('@alice')


def test_a_group_id_cannot_be_an_allowed_user():
    with pytest.raises(Access.AccessConfigError, match='group chat id'):
        load('-100123')


def test_op_chats_may_be_empty_and_group_ids_are_fine():
    load('11')
    assert Access.is_op_chat(11) is False
    load('11', '-100123')
    assert Access.is_op_chat(-100123) is True


def test_a_failed_load_keeps_the_previous_lists():
    load('11', '11')
    with pytest.raises(Access.AccessConfigError):
        load('')
    assert Access.is_allowed(11) and Access.is_op_chat(11)


def test_the_bot_refuses_to_start_without_an_allow_list(monkeypatch):
    monkeypatch.delenv('ALLOWED_USER_IDS', raising=False)
    started = []

    async def init():
        started.append('client')

    monkeypatch.setattr(main.Client, 'init', init)

    with pytest.raises(Access.AccessConfigError):
        asyncio.run(main.main())
    assert started == []


# ---- the gate, driven through PTB's own dispatcher with real Update objects (Telegram is not contacted)

def make_update(user_id, text, chat_id=500, sender=True):
    user = telegram.User(id=user_id, first_name='x', is_bot=False, username='someone') if sender else None
    chat = telegram.Chat(id=chat_id, type='private' if chat_id > 0 else 'supergroup')
    message = telegram.Message(message_id=1, date=None, chat=chat, from_user=user, text=text)
    if text.startswith('/'):
        message._unfreeze()
        message.entities = (telegram.MessageEntity(type='bot_command', offset=0, length=len(text.split()[0])),)
        message._freeze()
    update = telegram.Update(update_id=1, message=message)
    # Command matching calls message.get_bot(), so both objects need a bot or PTB raises before any handler runs.
    update.set_bot(Client.application.bot)
    message.set_bot(Client.application.bot)
    return update


@pytest.fixture
def seen(monkeypatch):
    calls = []

    async def record(name, *args):
        calls.append(name)

    for callback, name in [
        ('on_message_callback', 'message'), ('toggle_always_on_callback', 'stfu'), ('set_instance_model_callback', 'model'),
        ('toggle_auto_route_callback', 'autoroute'), ('set_instance_personality_callback', 'schizo'),
        ('list_memories_callback', 'memory'), ('forget_memory_callback', 'forget'), ('show_help_callback', 'help'),
    ]:
        async def handler(*args, _name=name, **kwargs):
            await record(_name, *args)
        monkeypatch.setattr(Client, callback, handler)
    return calls


@pytest.fixture(autouse=True)
def no_telegram(monkeypatch):
    # Application.initialize() would call Telegram's getMe. The bot's initialize/shutdown are replaced, and any
    # real HTTP request fails the test loudly.
    async def fake_initialize(self):
        # What getMe would have filled in. Command handlers compare against the bot's username.
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
    # caplog.records only holds the current phase (empty during teardown), so the test's own records are read.
    errors = [r for r in caplog.get_records('call') if r.name == 'telegram.ext.Application' and r.levelname == 'ERROR']
    assert errors == [], 'PTB logged an error while dispatching, so the result above proves nothing: ' + errors[0].getMessage()


def dispatch(update):
    async def run():
        await Client.application.initialize()
        try:
            await Client.application.process_update(update)
        finally:
            await Client.application.shutdown()

    asyncio.run(run())


def test_an_allowed_user_reaches_the_handlers(seen):
    load('11')

    dispatch(make_update(11, 'hello'))
    dispatch(make_update(11, '/help'))

    assert seen == ['message', 'help']


@pytest.mark.parametrize('text', ['hello', '/help', '/model opus-5.5-max', '/autoroute', '/schizo british', '/stfu', '/memory', '/forget 1', '/unknown'])
def test_a_stranger_reaches_nothing(seen, text):
    load('11')

    dispatch(make_update(99, text))

    assert seen == []


def test_an_update_with_no_sender_is_dropped(seen):
    load('11')

    dispatch(make_update(0, 'hello', sender=False))

    assert seen == []


def test_a_stranger_in_an_allowed_users_chat_is_still_dropped(seen):
    load('11', '-100123')

    dispatch(make_update(99, 'hello', chat_id=-100123))

    assert seen == []


def test_nothing_gets_through_before_the_lists_are_loaded(seen):
    dispatch(make_update(11, 'hello'))

    assert seen == []


def test_a_drop_is_logged_with_ids_and_never_the_text(seen, caplog):
    load('11')

    with caplog.at_level('WARNING', logger='client'):
        dispatch(make_update(99, 'my secret message', chat_id=-100123))

    assert 'user id 99' in caplog.text and 'chat id -100123' in caplog.text
    assert 'secret' not in caplog.text


def test_the_gate_runs_before_every_other_handler():
    groups = sorted(Client.application.handlers)

    assert groups[0] == -1
    assert [h.callback for h in Client.application.handlers[-1]] == [Client.gate]
    assert all(h.callback is not Client.gate for h in Client.application.handlers[0])
