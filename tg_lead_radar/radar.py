import logging

from .card import LeadCard, fit_stars, message_link, render_plain
from .filters import match
from .store import SeenStore
from .templates import pick_draft

logger = logging.getLogger(__name__)


def build_card(
    text: str,
    chat_id: int,
    msg_id: int,
    chat_title: str,
    chat_username: str | None,
    published: str = "",
) -> LeadCard | None:
    """Pure decision core: returns a LeadCard for a lead, or None.
    Separated from Telethon so it is fully testable."""
    result = match(text)
    if not result.matched:
        return None
    return LeadCard(
        channel_title=chat_title,
        link=message_link(chat_username, chat_id, msg_id),
        text=text,
        categories=result.categories,
        draft=pick_draft(result.categories),
        published=published,
        fit=fit_stars(result.categories, result.cat_hits),
    )


async def handle_event(event, store: SeenStore, notify_chat: str, client) -> bool:
    """Returns True when a card was sent."""
    text = event.raw_text or ""
    if not text:
        return False
    chat_id = event.chat_id
    if store.is_seen(chat_id, event.id):
        return False
    chat = await event.get_chat()
    date = getattr(event.message, "date", None)
    card = build_card(
        text=text,
        chat_id=chat_id,
        msg_id=event.id,
        chat_title=getattr(chat, "title", None) or getattr(chat, "username", "?") or "?",
        chat_username=getattr(chat, "username", None),
        published=date.isoformat() if date else "",
    )
    if card is None:
        return False
    if store.is_text_seen(text):
        logger.info("Duplicate text in %s/%s — skipped", chat_id, event.id)
        return False
    store.mark_text_seen(text)
    store.mark_seen(chat_id, event.id, card.categories)
    await client.send_message(notify_chat, render_plain(card), link_preview=False)
    logger.info(
        "Lead #%s in %s -> notified (%s)", event.id, chat_id, ",".join(card.categories)
    )
    return True
