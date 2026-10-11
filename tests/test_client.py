import asyncio
from types import SimpleNamespace

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


def test_failed_photo_download_without_caption_is_ignored():
    photo = [SimpleNamespace(file_id='a', width=100, height=100)]
    assert run_on_message(make_update(photo=photo), bot=FakeBot(fail=True)) == []


def test_failed_photo_download_with_caption_falls_back_to_text():
    photo = [SimpleNamespace(file_id='a', width=100, height=100)]
    calls = run_on_message(make_update(caption='look', photo=photo), bot=FakeBot(fail=True))
    assert calls == [(42, 'alice: look', None, False)]
