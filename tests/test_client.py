import asyncio
from types import SimpleNamespace

import pytest
import telegram

import client as Client


def make_update(text=None, caption=None, photo=None, reply=None):
    message = SimpleNamespace(
        chat=SimpleNamespace(id=42),
        message_id=1,
        from_user=SimpleNamespace(username='alice'),
        reply_to_message=reply,
        text=text,
        caption=caption,
        photo=photo or [],
    )
    return SimpleNamespace(message=message)


class FakeBot:
    def __init__(self, fail=False):
        self.fail = fail

    async def get_file(self, file_id):
        if self.fail:
            raise telegram.error.TelegramError('boom')
        return SimpleNamespace(download_to_memory=self._download)

    async def _download(self, buffer):
        buffer.write(b'jpegdata')


def run_on_message(update, bot=None):
    calls = []

    async def callback(chat_id, message, images, no_response):
        calls.append((chat_id, message, images, no_response))

    Client.on_message_callback = callback
    context = SimpleNamespace(bot=bot or FakeBot())
    asyncio.run(Client.on_message(update, context))
    return calls


def test_sticker_or_voice_message_is_ignored():
    assert run_on_message(make_update()) == []


def test_update_without_message_is_ignored():
    assert run_on_message(SimpleNamespace(message=None)) == []


def test_text_message_is_forwarded():
    calls = run_on_message(make_update(text='hi'))
    assert calls == [(42, 'alice: hi', None, False)]


def test_photo_without_caption_is_forwarded_with_image():
    photo = [SimpleNamespace(file_id='a', width=100, height=100)]
    calls = run_on_message(make_update(photo=photo))
    assert calls == [(42, 'alice: ', b'jpegdata', False)]


def test_the_largest_photo_size_is_downloaded():
    photo = [
        SimpleNamespace(file_id='small', width=90, height=60),
        SimpleNamespace(file_id='medium', width=320, height=240),
        SimpleNamespace(file_id='large', width=1280, height=960),
    ]
    asked = []

    class RecordingBot(FakeBot):
        async def get_file(self, file_id):
            asked.append(file_id)
            return await super().get_file(file_id)

    run_on_message(make_update(photo=photo), bot=RecordingBot())

    assert asked == ['large']


def test_failed_photo_download_without_caption_is_ignored():
    photo = [SimpleNamespace(file_id='a', width=100, height=100)]
    assert run_on_message(make_update(photo=photo), bot=FakeBot(fail=True)) == []


def test_failed_photo_download_with_caption_falls_back_to_text():
    photo = [SimpleNamespace(file_id='a', width=100, height=100)]
    calls = run_on_message(make_update(caption='look', photo=photo), bot=FakeBot(fail=True))
    assert calls == [(42, 'alice: look', None, False)]


class SendBot:
    def __init__(self, reject_markdown=False, reject_all=False):
        self.reject_markdown = reject_markdown
        self.reject_all = reject_all
        self.sent = []

    async def send_message(self, chat_id, text, parse_mode=None):
        if self.reject_all or (self.reject_markdown and parse_mode):
            raise telegram.error.BadRequest("Can't parse entities")
        assert 0 < len(text) <= 4096
        self.sent.append((text, parse_mode))


def run_send(text, bot, monkeypatch):
    monkeypatch.setattr(Client, 'application', SimpleNamespace(bot=bot))
    asyncio.run(Client.send_message(42, text))


def test_split_message_keeps_short_text_whole():
    assert Client.split_message('hello') == ['hello']


def test_split_message_cuts_long_text_into_pieces_within_the_limit_and_loses_nothing():
    text = '\n\n'.join(f'paragraph {i} ' + 'word ' * 150 for i in range(40))
    assert len(text) > 10000

    chunks = Client.split_message(text)

    assert len(chunks) > 1
    assert all(0 < len(chunk) <= 4096 for chunk in chunks)
    assert ' '.join(' '.join(chunks).split()) == ' '.join(text.split())


def test_split_message_prefers_a_blank_line_then_a_newline_then_a_space():
    first = 'a' * 3000
    second = 'b' * 3000
    assert Client.split_message(f'{first}\n\n{second}') == [first, second]
    assert Client.split_message(f'{first}\n{second}') == [first, second]
    assert Client.split_message(f'{first} {second}') == [first, second]


def test_split_message_hard_cuts_text_with_no_whitespace():
    chunks = Client.split_message('x' * 9000)
    assert [len(chunk) for chunk in chunks] == [4096, 4096, 808]
    assert ''.join(chunks) == 'x' * 9000


def test_send_message_sends_a_long_reply_as_several_messages(monkeypatch):
    bot = SendBot()
    text = '\n'.join('line ' * 40 for _ in range(100))

    run_send(text, bot, monkeypatch)

    assert len(bot.sent) > 1
    assert all(parse_mode for _, parse_mode in bot.sent)


@pytest.mark.parametrize('text', ['', '   \n '])
def test_send_message_does_not_send_an_empty_reply(text, monkeypatch):
    bot = SendBot()
    run_send(text, bot, monkeypatch)
    assert bot.sent == []


def test_send_message_retries_as_plain_text_and_logs_the_reason(monkeypatch, caplog):
    bot = SendBot(reject_markdown=True)

    run_send('hi rayhan_nurdjaman', bot, monkeypatch)

    assert bot.sent == [('hi rayhan_nurdjaman', None)]
    assert "Can't parse entities" in caplog.text


def test_send_message_raises_when_plain_text_fails_too(monkeypatch):
    with pytest.raises(telegram.error.BadRequest):
        run_send('hi', SendBot(reject_all=True), monkeypatch)
