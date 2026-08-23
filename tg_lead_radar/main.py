import asyncio
import logging
from pathlib import Path

from telethon import TelegramClient, events

from .config import load_settings
from .radar import handle_event
from .store import SeenStore

logger = logging.getLogger(__name__)


async def run() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    settings = load_settings()
    Path(settings.session_path).parent.mkdir(parents=True, exist_ok=True)
    store = SeenStore(settings.db_path)

    client = TelegramClient(settings.session_path, settings.api_id, settings.api_hash)
    await client.start()  # first run asks for phone + login code interactively

    channels = settings.channel_list()
    resolved = []
    for name in channels:
        try:
            entity = await client.get_entity(name)
            resolved.append(entity)
            logger.info("Watching @%s (%s)", name, getattr(entity, "title", ""))
        except Exception:
            logger.exception("Cannot resolve @%s — skipped (join the chat first?)", name)

    if not resolved:
        raise SystemExit("No channels resolved, nothing to watch")

    @client.on(events.NewMessage(chats=resolved))
    async def _on_message(event) -> None:
        try:
            await handle_event(event, store, settings.notify_chat, client)
        except Exception:
            logger.exception("Failed to process message %s", event.id)

    logger.info("Radar is up: %d chats, notifying %s", len(resolved), settings.notify_chat)
    try:
        await client.run_until_disconnected()
    finally:
        store.close()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
