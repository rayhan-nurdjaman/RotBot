import asyncio
import json
from types import SimpleNamespace

import pytest

import assistant as Assistant
import db
import main
import memories as Memories
from instance import Instance
from tools.native import registry
from tools.registry import ToolContext


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / 'rotbot.db')


def with_db(path):
    """Runs an async test body with an open database, closing it afterwards."""
    def decorator(body):
        async def go():
            await db.init(path)
            try:
                return await body()
            finally:
                await db.close()
        return asyncio.run(go())
    return decorator


def message_text(message):
    return message['content'][0]['text']


# --- the store ---------------------------------------------------------------------------------------------

def test_add_saves_and_lists_in_order(db_path):
    @with_db(db_path)
    async def body():
        first, created_first = await Memories.add(1, 'alice likes tea')
        second, created_second = await Memories.add(1, 'bob plays chess')
        entries, hidden = await Memories.for_prompt(1)
        assert (created_first, created_second) == (True, True)
        assert [e['id'] for e in entries] == [first, second]
        assert [e['text'] for e in entries] == ['alice likes tea', 'bob plays chess']
        assert hidden == 0


def test_memories_are_scoped_to_their_chat(db_path):
    @with_db(db_path)
    async def body():
        memory_id, _ = await Memories.add(1, 'alice likes tea')
        assert await Memories.for_prompt(2) == ([], 0)
        assert await Memories.search(2, 'tea') == []
        assert await Memories.delete(2, memory_id) is False
        assert len((await Memories.for_prompt(1))[0]) == 1


def test_delete_removes_only_the_given_memory(db_path):
    @with_db(db_path)
    async def body():
        keep, _ = await Memories.add(1, 'one')
        gone, _ = await Memories.add(1, 'two')
        assert await Memories.delete(1, gone) is True
        assert await Memories.delete(1, gone) is False
        assert [e['id'] for e in (await Memories.for_prompt(1))[0]] == [keep]


def test_exact_duplicates_are_not_saved_twice(db_path):
    @with_db(db_path)
    async def body():
        first, _ = await Memories.add(1, 'alice likes tea')
        again, created = await Memories.add(1, '  Alice   likes TEA ')
        assert (again, created) == (first, False)
        assert len((await Memories.for_prompt(1))[0]) == 1
        # The same text in another chat is a different memory.
        other, created = await Memories.add(2, 'alice likes tea')
        assert created is True and other != first


def test_empty_and_overlong_text_is_rejected(db_path):
    @with_db(db_path)
    async def body():
        with pytest.raises(Memories.Rejected, match='empty'):
            await Memories.add(1, '   ')
        with pytest.raises(Memories.Rejected, match=str(Memories.MAX_LENGTH)):
            await Memories.add(1, 'x' * (Memories.MAX_LENGTH + 1))
        await Memories.add(1, 'x' * Memories.MAX_LENGTH)


@pytest.mark.parametrize('text', [
    'alice card number is 4111 1111 1111 1111',
    'her card: 4111-1111-1111-1111',
    'bob key is sk-or-v1-abcdefghijklmnop1234',
    'the wifi password is hunter2',
    'Password: hunter2',
    'alice api key = abc123',
    '-----BEGIN RSA PRIVATE KEY-----',
])
def test_obvious_secrets_are_rejected(db_path, text):
    @with_db(db_path)
    async def body():
        with pytest.raises(Memories.Rejected, match='password, key or card'):
            await Memories.add(1, text)
        assert await Memories.for_prompt(1) == ([], 0)


@pytest.mark.parametrize('text', [
    'alice likes tea',
    'bob was born on 1990-05-12',
    'carol reads the news every morning, in a token gesture of habit',
    'dave started in 2019 and has 3 cats',
    'reply in a formal tone',
])
def test_ordinary_text_is_not_mistaken_for_a_secret(db_path, text):
    @with_db(db_path)
    async def body():
        await Memories.add(1, text)


def test_a_full_chat_refuses_more_and_other_chats_are_unaffected(db_path, monkeypatch):
    monkeypatch.setattr(Memories, 'MAX_PER_CHAT', 3)

    @with_db(db_path)
    async def body():
        for number in range(3):
            await Memories.add(1, f'fact {number}')
        with pytest.raises(Memories.Rejected, match='forget'):
            await Memories.add(1, 'one too many')
        assert len((await Memories.for_prompt(1))[0]) == 3
        await Memories.add(2, 'fine')


def test_the_prompt_gets_the_newest_within_the_caps_and_says_how_many_were_left_out(db_path):
    @with_db(db_path)
    async def body():
        for number in range(5):
            await Memories.add(1, f'fact {number}')
        entries, hidden = await Memories.for_prompt(1, count=3)
        assert [e['text'] for e in entries] == ['fact 2', 'fact 3', 'fact 4']
        assert hidden == 2

        entries, hidden = await Memories.for_prompt(1, count=10, max_chars=len('fact 3') + len('fact 4'))
        assert [e['text'] for e in entries] == ['fact 3', 'fact 4']
        assert hidden == 3


def test_search_ranks_by_matching_words_then_newest(db_path):
    @with_db(db_path)
    async def body():
        await Memories.add(1, 'alice likes tea')
        await Memories.add(1, 'alice likes coffee and tea')
        await Memories.add(1, 'bob plays chess')
        await Memories.add(1, 'carol drinks tea')

        found = [e['text'] for e in await Memories.search(1, 'tea coffee')]
        assert found == ['alice likes coffee and tea', 'carol drinks tea', 'alice likes tea']
        assert [e['text'] for e in await Memories.search(1, 'CHESS')] == ['bob plays chess']
        assert await Memories.search(1, 'nothing matches') == []


def test_an_empty_query_returns_the_newest_up_to_the_limit(db_path):
    @with_db(db_path)
    async def body():
        for number in range(15):
            await Memories.add(1, f'fact {number}')
        found = await Memories.search(1, '')
        assert len(found) == Memories.RECALL_LIMIT
        assert found[0]['text'] == 'fact 14'


def test_memories_survive_reopening_the_database(db_path):
    @with_db(db_path)
    async def first():
        await Memories.add(1, 'alice likes tea')

    @with_db(db_path)
    async def second():
        assert [e['text'] for e in (await Memories.for_prompt(1))[0]] == ['alice likes tea']


# --- how the model sees them -------------------------------------------------------------------------------

def test_no_memories_adds_nothing_to_the_request():
    instance = Instance('chat-1')
    instance.add_user_message('alice: hi')

    assert instance.get_memory_messages() == []
    assert [m['role'] for m in instance.get_context()] == ['system', 'user']


def test_the_memory_message_lists_ids_and_goes_right_after_the_system_prompt(db_path):
    instance = Instance('chat-1')

    @with_db(db_path)
    async def body():
        await Memories.add(1, 'x')
        await Memories.add('chat-1', 'alice likes tea')
        await instance.load_memory()

    instance.add_user_message('alice: hi')
    context = instance.get_context()

    assert [m['role'] for m in context] == ['system', 'user', 'user']
    assert '- [2] alice likes tea' in message_text(context[1])
    assert 'standing instructions' in message_text(context[1])
    assert 'alice: hi' in message_text(context[2])
    assert all('alice likes tea' not in str(m) for m in instance.context_window)
    assert 'alice likes tea' not in message_text(instance.get_system_prompt()[0])


def test_hidden_memories_are_mentioned_with_a_pointer_to_recall_memory():
    instance = Instance('chat-1')
    instance.memory = [{'id': 7, 'text': 'newest'}]
    instance.memory_hidden = 4

    text = message_text(instance.get_memory_messages()[0])

    assert '4 older memories are not shown' in text and 'recall_memory' in text


def test_a_turn_loads_memory_once_and_a_memory_saved_in_the_turn_appears_from_the_next_turn(db_path, monkeypatch):
    seen = []
    responses = iter([
        SimpleNamespace(finish_reason='tool_calls', message=SimpleNamespace(content='', tool_calls=[
            SimpleNamespace(id='a', function=SimpleNamespace(name='save_memory', arguments='{"text": "alice likes tea"}'))], model_extra={})),
        SimpleNamespace(finish_reason='stop', message=SimpleNamespace(content='saved', tool_calls=None, model_extra={})),
        SimpleNamespace(finish_reason='stop', message=SimpleNamespace(content='ok', tool_calls=None, model_extra={})),
    ])

    async def create(messages, model, tools=None, chat_id=None, tool_choice=None):
        seen.append(json.dumps(messages[:2]))
        return next(responses)

    monkeypatch.setattr(Assistant.ChatCompletion, 'create', create)
    instance = Instance('chat-1')

    @with_db(db_path)
    async def body():
        await Memories.add('chat-1', 'bob plays chess')
        instance.add_user_message('alice: remember that I like tea')
        await Assistant.get_response(instance)
        instance.add_user_message('alice: hi')
        await Assistant.get_response(instance)

    # seen holds the system prompt and the memory message of each request. Both requests of the first turn have
    # the same front, so the prompt cache holds through the tool loop.
    assert seen[0] == seen[1]
    assert 'bob plays chess' in seen[0] and 'alice likes tea' not in seen[0]
    assert 'bob plays chess' in seen[2] and 'alice likes tea' in seen[2]


def test_a_failing_database_does_not_stop_the_turn(monkeypatch, caplog):
    instance = Instance('chat-1')
    instance.memory = [{'id': 1, 'text': 'old'}]

    asyncio.run(instance.load_memory())  # no database is open here

    assert instance.memory == [{'id': 1, 'text': 'old'}]
    assert 'Could not load the memories' in caplog.text


# --- tools -------------------------------------------------------------------------------------------------

def call(name, arguments, chat_id='chat-1'):
    return registry.call(name, arguments, ToolContext(Instance(chat_id), None))


def test_the_tools_save_recall_and_forget(db_path):
    @with_db(db_path)
    async def body():
        saved = await call('save_memory', {'text': 'alice likes tea'})
        assert saved['ok'] is True and saved['already_saved'] is False
        again = await call('save_memory', {'text': 'alice likes tea'})
        assert again['id'] == saved['id'] and again['already_saved'] is True

        recalled = await call('recall_memory', {'query': 'tea'})
        assert recalled == {'ok': True, 'memories': [{'id': saved['id'], 'text': 'alice likes tea'}]}

        assert await call('forget_memory', {'id': saved['id']}) == {'ok': True, 'forgotten': saved['id']}
        assert (await call('recall_memory', {'query': ''}))['memories'] == []


def test_save_memory_reports_a_rejection_to_the_model(db_path):
    @with_db(db_path)
    async def body():
        result = await call('save_memory', {'text': 'the password is hunter2'})
        assert result['ok'] is False and 'password' in result['error']


def test_a_chat_cannot_forget_another_chats_memory(db_path):
    @with_db(db_path)
    async def body():
        saved = await call('save_memory', {'text': 'alice likes tea'}, chat_id='chat-1')
        result = await call('forget_memory', {'id': saved['id']}, chat_id='chat-2')
        assert result['ok'] is False and str(saved['id']) in result['error']
        assert (await call('recall_memory', {'query': ''}, chat_id='chat-1'))['memories'] != []


# --- commands ----------------------------------------------------------------------------------------------

@pytest.fixture
def sent(monkeypatch):
    messages = []

    async def send_message(chat_id, message):
        messages.append((chat_id, message))

    monkeypatch.setattr(main.Client, 'send_message', send_message)
    return messages


def test_memory_command_with_nothing_saved(db_path, sent):
    @with_db(db_path)
    async def body():
        await main.list_memories(1)

    assert sent == [(1, '_Nothing is saved in memory for this chat._')]


def test_memory_command_lists_ids_and_only_this_chats_entries(db_path, sent):
    @with_db(db_path)
    async def body():
        await Memories.add(1, 'alice likes tea')
        await Memories.add(2, 'secret of the other chat')
        await Memories.add(1, 'reply in a formal tone')
        await main.list_memories(1)

    (chat_id, text), = sent
    assert chat_id == 1
    assert '1. alice likes tea' in text and '3. reply in a formal tone' in text
    assert 'other chat' not in text


def test_memory_command_stays_under_the_telegram_message_limit(db_path, sent):
    @with_db(db_path)
    async def body():
        for number in range(Memories.MAX_PER_CHAT):
            await Memories.add(1, f'{number} ' + 'x' * (Memories.MAX_LENGTH - 10))
        await main.list_memories(1)

    (_, text), = sent
    assert len(text) < 4096
    assert 'older memories are not shown' in text


def test_forget_command_deletes_and_reports(db_path, sent):
    @with_db(db_path)
    async def body():
        memory_id, _ = await Memories.add(1, 'alice likes tea')
        await main.forget_memory(1, str(memory_id))
        await main.forget_memory(1, str(memory_id))
        await main.forget_memory(1, '')
        await main.forget_memory(1, 'abc')
        assert await Memories.for_prompt(1) == ([], 0)

    assert [text for _, text in sent] == [
        '_Forgot memory 1._',
        '_There is no memory 1 in this chat._',
        '_Usage: /forget <id>, the id is the number shown by /memory._',
        '_Usage: /forget <id>, the id is the number shown by /memory._',
    ]


def test_forget_command_cannot_delete_another_chats_memory(db_path, sent):
    @with_db(db_path)
    async def body():
        memory_id, _ = await Memories.add(1, 'alice likes tea')
        await main.forget_memory(2, str(memory_id))
        assert len((await Memories.for_prompt(1))[0]) == 1

    assert sent == [(2, '_There is no memory 1 in this chat._')]
