from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable
from pathlib import Path

from aiogram import Bot, Dispatcher, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from .config import Settings, normalize_login
from .store import Store

log = logging.getLogger(__name__)

LOGIN = re.compile(r"^[a-z0-9_]{3,25}$")

HELP = (
    "Я записываю стримы и присылаю сюда моменты, на которых чат взрывается.\n\n"
    "/list - стримеры и кто сейчас в эфире\n"
    "/add логин - добавить стримера\n"
    "/remove логин - убрать стримера"
)


def vote_keyboard(clip_id: int, likes: int = 0, dislikes: int = 0) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"👍 {likes}" if likes else "👍", callback_data=f"v:{clip_id}:1"
                ),
                InlineKeyboardButton(
                    text=f"👎 {dislikes}" if dislikes else "👎", callback_data=f"v:{clip_id}:-1"
                ),
            ]
        ]
    )


_ICONS = {"recording": "🔴", "other_game": "🟡", "offline": "⚫"}


def streamers_view(
    streamers: list[tuple[str, bool]], statuses: dict[str, tuple[str, str]]
) -> tuple[str, InlineKeyboardMarkup | None]:
    if not streamers:
        return "Список пуст. Добавь стримера: /add логин", None
    lines = []
    for name, enabled in streamers:
        state, game = statuses.get(name, ("offline", ""))
        line = f"{_ICONS[state]} {name}"
        if game:
            line += f" · {game}"
        lines.append(line + (" ✂️" if enabled else ""))
    buttons = [
        [
            InlineKeyboardButton(
                text=f"⏹ Не резать {name}" if enabled else f"🎬 Резать {name}",
                callback_data=f"t:{name}",
            )
        ]
        for name, enabled in streamers
    ]
    text = "🔴 пишу · 🟡 в эфире, но другая игра · ✂️ включён\n\n" + "\n".join(lines)
    return text, InlineKeyboardMarkup(inline_keyboard=buttons)


class ClipBot:
    def __init__(
        self,
        cfg: Settings,
        store: Store,
        statuses: Callable[[], dict[str, tuple[str, str]]],
        on_change: Callable[[], Awaitable[None]],
    ) -> None:
        self.cfg = cfg
        self.store = store
        self._statuses = statuses
        self._on_change = on_change
        self.bot = Bot(cfg.bot_token)
        self.dp = Dispatcher()
        self.dp.include_router(self._router())

    async def run(self) -> None:
        await self.dp.start_polling(self.bot, handle_signals=False)

    async def send_clip(self, path: Path, caption: str, clip_id: int, vertical: bool) -> None:
        width, height = (720, 1280) if vertical else (1280, 720)
        await self.bot.send_video(
            self.cfg.chat_id,
            FSInputFile(path),
            caption=caption,
            width=width,
            height=height,
            supports_streaming=True,
            reply_markup=vote_keyboard(clip_id),
            request_timeout=300,
        )

    def _router(self) -> Router:
        router = Router()
        cfg, store = self.cfg, self.store

        if not cfg.chat_id:
            # Setup mode: the only thing the bot does is tell which chat it is in.
            @router.message(Command("start", "id"))
            async def setup(message: Message) -> None:
                await message.answer(
                    f"id этого чата: {message.chat.id}\n"
                    "Впиши его в .env как CHAT_ID и перезапусти бота."
                )

            return router

        # Everything below works only in the configured chat.
        router.message.filter(F.chat.id == cfg.chat_id)
        router.callback_query.filter(F.message.chat.id == cfg.chat_id)

        @router.message(Command("start", "help"))
        async def start(message: Message) -> None:
            await message.answer(HELP)

        @router.message(Command("list"))
        async def list_streamers(message: Message) -> None:
            text, keyboard = streamers_view(store.streamers(), self._statuses())
            await message.answer(text, reply_markup=keyboard)

        @router.message(Command("add"))
        async def add(message: Message, command: CommandObject) -> None:
            names = [normalize_login(x) for x in (command.args or "").replace(",", " ").split()]
            if not names:
                await message.answer("Напиши логин: /add buster")
                return
            bad = [n for n in names if not LOGIN.match(n)]
            if bad:
                await message.answer(f"Не похоже на логин Twitch: {', '.join(bad)}")
                return
            added = [n for n in names if store.add_streamer(n)]
            await self._on_change()
            if added:
                await message.answer(f"Добавил: {', '.join(added)}")
            else:
                await message.answer("Они уже в списке.")

        @router.message(Command("remove"))
        async def remove(message: Message, command: CommandObject) -> None:
            name = normalize_login(command.args or "")
            if not name:
                await message.answer("Напиши логин: /remove buster")
                return
            if store.remove_streamer(name):
                await self._on_change()
                await message.answer(f"Убрал {name}.")
            else:
                await message.answer(f"{name} нет в списке.")

        @router.callback_query(F.data.startswith("t:"))
        async def toggle(query: CallbackQuery) -> None:
            name = (query.data or "")[2:]
            state = store.toggle_streamer(name)
            if state is None:
                await query.answer("Этого стримера уже нет в списке")
                return
            await self._on_change()
            await query.answer(f"{'Режу' if state else 'Не режу'} {name}")
            text, keyboard = streamers_view(store.streamers(), self._statuses())
            if isinstance(query.message, Message):
                try:
                    await query.message.edit_text(text, reply_markup=keyboard)
                except TelegramBadRequest as e:
                    log.debug("list not updated: %s", e)

        @router.callback_query(F.data.startswith("v:"))
        async def vote(query: CallbackQuery) -> None:
            try:
                _, clip_id, value = (query.data or "").split(":")
                likes, dislikes = store.vote(int(clip_id), query.from_user.id, int(value))
            except ValueError:
                await query.answer()
                return
            await query.answer("Учёл")
            if isinstance(query.message, Message):
                try:
                    await query.message.edit_reply_markup(
                        reply_markup=vote_keyboard(int(clip_id), likes, dislikes)
                    )
                except TelegramBadRequest as e:
                    log.debug("votes not updated: %s", e)

        return router
