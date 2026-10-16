"""Userbot mode: reads channels AND public chats through your own account.

Needs my.telegram.org credentials and a one-time login (scripts/login.py).
Sources are polled rather than subscribed to, so nothing is added to your
Telegram and chats invisible to the t.me/s web mode still get covered.

One process serves both modes — freelance orders and QA vacancies — because
a Telethon session file cannot be shared: a second process on it gets
"database is locked". ORDERS_ENABLED / JOBS_ENABLED in .env pick the modes.
"""

import asyncio
import logging
from pathlib import Path

import httpx
from telethon import TelegramClient

from .config import Settings, load_settings
from .jobs.collector import JobsRadar
from .jobs.config import load_jobs_config
from .jobs.store import JobsStore
from .store import SeenStore
from .userbot import poll_sources
from .web import acquire_single_instance_lock

logger = logging.getLogger(__name__)


async def resolve_order_sources(client, settings: Settings) -> list:
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
    return entities


async def run_cycle(
    client,
    order_entities: list,
    jobs: JobsRadar | None,
    store: SeenStore,
    settings: Settings,
    http: httpx.Client,
) -> None:
    """One poll of every enabled mode. A failure in one mode never stops the other."""
    if order_entities:
        try:
            sent = await poll_sources(client, order_entities, store, settings, http)
        except Exception:
            logger.exception("Orders poll failed")
        else:
            if sent:
                logger.info("Poll cycle done: %d cards sent", sent)
    if jobs is not None:
        try:
            found = await jobs.poll()
        except Exception:
            logger.exception("Jobs poll failed")
        else:
            if found:
                logger.info("Jobs: %d new vacancies", found)


async def run() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    settings = load_settings()
    if not settings.api_id or not settings.api_hash:
        raise SystemExit("Нет API_ID / API_HASH в .env — возьмите их на my.telegram.org")
    if not settings.orders_enabled and not settings.jobs_enabled:
        raise SystemExit("Оба режима выключены: включите ORDERS_ENABLED или JOBS_ENABLED в .env")

    Path(settings.session_path).parent.mkdir(parents=True, exist_ok=True)
    lock = acquire_single_instance_lock(str(Path(settings.db_path).with_name("radar.lock")))
    store = SeenStore(settings.db_path)
    jobs_store = None

    client = TelegramClient(settings.session_path, settings.api_id, settings.api_hash)
    await client.start()  # first run asks for phone + login code interactively

    entities = await resolve_order_sources(client, settings) if settings.orders_enabled else []
    jobs = None
    if settings.jobs_enabled:
        jobs_store = JobsStore(settings.jobs_db_path)
        jobs = JobsRadar(
            client,
            jobs_store,
            settings,
            load_jobs_config(settings.jobs_config_path),
            known_handles=settings.channel_list(),
        )
        await jobs.refresh_sources(force=True)

    if not entities and not (jobs and jobs.entities):
        raise SystemExit("No sources resolved, nothing to watch")

    http = httpx.Client(timeout=300)
    logger.info(
        "Radar is up: orders %s (%d sources), jobs %s (%d sources), every %ss, "
        "notifying %s, LLM %s",
        "on" if settings.orders_enabled else "off",
        len(entities),
        "on" if jobs else "off",
        len(jobs.entities) if jobs else 0,
        settings.poll_interval_seconds,
        settings.notify_chat,
        "on" if settings.llm_enabled else "off",
    )
    try:
        while True:
            await run_cycle(client, entities, jobs, store, settings, http)
            await asyncio.sleep(settings.poll_interval_seconds)
    except KeyboardInterrupt:
        logger.info("Stopped")
    finally:
        http.close()
        store.close()
        if jobs_store:
            jobs_store.close()
        lock.close()
        await client.disconnect()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
