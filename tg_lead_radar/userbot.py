"""Userbot polling: read sources through your own account, joined or not.

Telethon delivers NewMessage events only for dialogs the account belongs to,
so an event-driven radar would go silent on every channel you never
subscribed to. Reading history works regardless, so we poll instead: the
account stays clean (no forced subscriptions) and public chats become
visible, which the t.me/s web mode cannot do at all.
"""

import asyncio
import logging

import httpx
from telethon.errors import FloodWaitError

from .card import LeadCard, age_hours, render_plain
from .config import Settings
from .llm import enrich_card
from .radar import build_card
from .store import SeenStore

logger = logging.getLogger(__name__)

FETCH_LIMIT = 25
FIRST_POLL_MAX_AGE_HOURS = 48
FIRST_POLL_MAX_CARDS = 10


def _trim(cards: list[LeadCard]) -> list[LeadCard]:
    fresh = [c for c in cards if (age_hours(c.published) or 0) <= FIRST_POLL_MAX_AGE_HOURS]
    return fresh[-FIRST_POLL_MAX_CARDS:]


async def collect_new_cards(
    client, entity, store: SeenStore, settings: Settings
) -> list[LeadCard]:
    """New leads from one source, already deduplicated and marked seen."""
    username = getattr(entity, "username", None)
    chat_key = username or entity.id
    title = getattr(entity, "title", None) or username or str(entity.id)
    # In a group people phrase orders in plain speech, so keywords would gate
    # too much — let the model decide there, as in the order feeds.
    judge_all = settings.llm_enabled and (
        (username or "") in settings.order_feed_list()
        or getattr(entity, "megagroup", False)
    )
    first_time = not store.has_chat(chat_key)

    messages = await client.get_messages(entity, limit=FETCH_LIMIT)
    cards: list[LeadCard] = []
    for message in sorted(messages, key=lambda m: m.id):
        text = getattr(message, "message", None)
        if not text or store.is_seen(chat_key, message.id):
            continue
        card = build_card(
            text=text,
            chat_id=entity.id,
            msg_id=message.id,
            chat_title=title,
            chat_username=username,
            published=message.date.isoformat() if message.date else "",
            force=judge_all,
        )
        store.mark_seen(chat_key, message.id, card.categories if card else [])
        if card is None:
            continue
        if store.is_text_seen(text):
            logger.info("Duplicate text in %s/%s — skipped", chat_key, message.id)
            continue
        store.mark_text_seen(text)
        cards.append(card)

    if first_time:
        trimmed = _trim(cards)
        if len(trimmed) != len(cards):
            logger.info("%s: first poll, keeping %d of %d (fresh only)",
                        title, len(trimmed), len(cards))
        cards = trimmed
    return cards


async def poll_sources(
    client, entities: list, store: SeenStore, settings: Settings, http: httpx.Client
) -> int:
    sent = 0
    for entity in entities:
        try:
            cards = await collect_new_cards(client, entity, store, settings)
        except FloodWaitError as exc:
            logger.warning("Flood wait %ss on %s — pausing", exc.seconds, entity.id)
            await asyncio.sleep(exc.seconds + 1)
            continue
        except Exception:
            logger.exception("Failed to read %s", getattr(entity, "username", entity.id))
            continue

        for card in cards:
            if settings.llm_enabled:
                # judge_lead blocks on HTTP; keep the event loop free.
                judged = await asyncio.to_thread(enrich_card, http, settings, card)
                if judged is None:
                    continue
                card = judged
            try:
                await client.send_message(
                    settings.notify_chat, render_plain(card), link_preview=False
                )
                sent += 1
            except Exception:
                logger.exception("Failed to notify about %s", card.link)
        await asyncio.sleep(1)  # be gentle with the API between sources
    return sent
