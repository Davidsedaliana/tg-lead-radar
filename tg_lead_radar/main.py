"""Userbot mode: reads channels AND public chats through your own account.

Needs my.telegram.org credentials and a one-time login (scripts/login.py).
Sources are polled rather than subscribed to, so nothing is added to your
Telegram and chats invisible to the t.me/s web mode still get covered.
"""

import asyncio
import logging
from pathlib import Path

import httpx
from telethon import TelegramClient

from .config import load_settings
from .store import SeenStore
from .userbot import poll_sources
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

    entities = []
    for name in settings.channel_list():
        try:
            entity = await client.get_entity(name)
        except Exception:
            logger.exception("Cannot resolve @%s — skipped", name)
        else:
            entities.append(entity)
            kind = "чат" if getattr(entity, "megagroup", False) else "канал"
            logger.info("Watching @%s — %s (%s)", name, getattr(entity, "title", ""), kind)

    if not entities:
        raise SystemExit("No sources resolved, nothing to watch")

    http = httpx.Client(timeout=300)
    logger.info(
        "Radar is up: %d sources, every %ss, notifying %s, LLM %s",
        len(entities),
        settings.poll_interval_seconds,
        settings.notify_chat,
        "on" if settings.llm_enabled else "off",
    )
    try:
        while True:
            sent = await poll_sources(client, entities, store, settings, http)
            if sent:
                logger.info("Poll cycle done: %d cards sent", sent)
            await asyncio.sleep(settings.poll_interval_seconds)
    except KeyboardInterrupt:
        logger.info("Stopped")
    finally:
        http.close()
        store.close()
        lock.close()
        await client.disconnect()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
