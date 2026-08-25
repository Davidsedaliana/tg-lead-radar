"""Userbot mode: reads channels AND private chats through your own account.

Needs my.telegram.org credentials and a one-time login (scripts/login.py).
Unlike web mode it sees chats, has no polling delay, and notifies into
Saved Messages.
"""

import asyncio
import logging
from pathlib import Path

import httpx
from telethon import TelegramClient, events

from .config import load_settings
from .radar import handle_event
from .store import SeenStore
from .web import acquire_single_instance_lock

logger = logging.getLogger(__name__)


async def run() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    settings = load_settings()
    if not settings.api_id or not settings.api_hash:
        raise SystemExit("Нет API_ID / API_HASH в .env — возьмите их на my.telegram.org")

    Path(settings.session_path).parent.mkdir(parents=True, exist_ok=True)
    lock = acquire_single_instance_lock(str(Path(settings.db_path).with_name("radar.lock")))
    store = SeenStore(settings.db_path)

    client = TelegramClient(settings.session_path, settings.api_id, settings.api_hash)
    await client.start()  # first run asks for phone + login code interactively

    resolved = []
    for name in settings.channel_list():
        try:
            entity = await client.get_entity(name)
        except Exception:
            logger.exception("Cannot resolve @%s — skipped (join the chat first?)", name)
        else:
            resolved.append(entity)
            logger.info("Watching @%s (%s)", name, getattr(entity, "title", ""))

    if not resolved:
        raise SystemExit("No channels resolved, nothing to watch")

    http = httpx.Client(timeout=300)

    @client.on(events.NewMessage(chats=resolved))
    async def _on_message(event) -> None:
        try:
            await handle_event(event, store, settings, client, http)
        except Exception:
            logger.exception("Failed to process message %s", event.id)

    logger.info(
        "Radar is up: %d chats, notifying %s, LLM %s",
        len(resolved),
        settings.notify_chat,
        "on" if settings.llm_enabled else "off",
    )
    try:
        await client.run_until_disconnected()
    finally:
        http.close()
        store.close()
        lock.close()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
