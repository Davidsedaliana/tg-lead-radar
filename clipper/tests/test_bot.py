"""Bot handlers driven through aiogram with a fake Telegram session (no network)."""

import datetime

from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.methods import (
    AnswerCallbackQuery,
    EditMessageReplyMarkup,
    EditMessageText,
    SendMessage,
)
from aiogram.types import Chat, Message

from twitch_clipper.bot import ClipBot
from twitch_clipper.config import Settings
from twitch_clipper.store import Store

CHAT = -100500
TOKEN = "123456:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"


class FakeSession(BaseSession):
    def __init__(self) -> None:
        super().__init__()
        self.calls: list = []

    async def close(self) -> None:
        pass

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        if isinstance(method, SendMessage):
            return Message(
                message_id=len(self.calls),
                date=datetime.datetime.now(tz=datetime.UTC),
                chat=Chat(id=method.chat_id, type="supergroup"),
                text=method.text,
            )
        return True

    async def stream_content(self, *args, **kwargs):
        yield b""


class Harness:
    def __init__(self, chat_id: int = CHAT) -> None:
        self.store = Store(":memory:")
        self.changes = 0
        self.online: set[str] = set()
        self.session = FakeSession()
        self.clipbot = ClipBot(
            Settings(bot_token=TOKEN, chat_id=chat_id),
            self.store,
            online=lambda: self.online,
            on_change=self._changed,
        )
        self.bot = Bot(TOKEN, session=self.session)
        self._update_id = 0

    async def _changed(self) -> None:
        self.changes += 1

    def _message(self, text: str, chat_id: int) -> dict:
        return {
            "message_id": 1,
            "date": 1795000000,
            "chat": {"id": chat_id, "type": "supergroup"},
            "from": {"id": 7, "is_bot": False, "first_name": "D"},
            "text": text,
            "entities": [{"type": "bot_command", "offset": 0, "length": len(text.split()[0])}],
        }

    async def _feed(self, **payload) -> list:
        self._update_id += 1
        self.session.calls.clear()
        await self.clipbot.dp.feed_raw_update(self.bot, {"update_id": self._update_id, **payload})
        return list(self.session.calls)

    async def say(self, text: str, chat_id: int = CHAT) -> list:
        return await self._feed(message=self._message(text, chat_id))

    async def press(self, data: str, chat_id: int = CHAT, user_id: int = 7) -> list:
        return await self._feed(
            callback_query={
                "id": "1",
                "from": {"id": user_id, "is_bot": False, "first_name": "D"},
                "chat_instance": "x",
                "data": data,
                "message": self._message("/list", chat_id),
            }
        )


async def test_setup_mode_reports_chat_id():
    h = Harness(chat_id=0)
    [reply] = await h.say("/start", chat_id=-42)
    assert "-42" in reply.text
    assert await h.say("/list", chat_id=-42) == []  # nothing else works until CHAT_ID is set


async def test_commands_work_only_in_the_configured_chat():
    h = Harness()
    assert await h.say("/add buster", chat_id=999) == []
    assert h.store.streamers() == []
    assert await h.press("t:buster", chat_id=999) == []


async def test_add_list_remove():
    h = Harness()
    [reply] = await h.say("/add Buster https://twitch.tv/steel")
    assert reply.text == "Добавил: buster, steel"
    assert h.changes == 1
    [reply] = await h.say("/add не логин!")
    assert "Не похоже на логин" in reply.text

    h.online = {"steel"}
    [reply] = await h.say("/list")
    assert "⚫ buster ✂️" in reply.text and "🔴 steel ✂️" in reply.text
    assert len(reply.reply_markup.inline_keyboard) == 2

    [reply] = await h.say("/remove steel")
    assert reply.text == "Убрал steel."
    assert h.store.streamers() == [("buster", True)]
    assert h.changes == 2


async def test_toggle_button_updates_list():
    h = Harness()
    h.store.add_streamer("buster")
    answer, edit = await h.press("t:buster")
    assert isinstance(answer, AnswerCallbackQuery) and answer.text == "Не режу buster"
    assert isinstance(edit, EditMessageText)
    assert edit.reply_markup.inline_keyboard[0][0].text == "🎬 Резать buster"
    assert h.store.enabled_streamers() == set()
    assert h.changes == 1


async def test_vote_buttons_count_votes():
    h = Harness()
    clip = h.store.add_clip("buster", 3.0, None, 8, "funny", "x")
    await h.press(f"v:{clip}:1", user_id=1)
    answer, edit = await h.press(f"v:{clip}:1", user_id=2)
    assert isinstance(answer, AnswerCallbackQuery)
    assert isinstance(edit, EditMessageReplyMarkup)
    assert [b.text for b in edit.reply_markup.inline_keyboard[0]] == ["👍 2", "👎"]
    assert h.store.votes(clip) == (2, 0)
