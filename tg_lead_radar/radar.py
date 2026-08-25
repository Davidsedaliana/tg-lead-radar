import logging

from .card import LeadCard, fit_stars, message_link
from .filters import match
from .templates import pick_draft

logger = logging.getLogger(__name__)


def build_card(
    text: str,
    chat_id: int,
    msg_id: int,
    chat_title: str,
    chat_username: str | None,
    published: str = "",
    force: bool = False,
) -> LeadCard | None:
    """Pure decision core: returns a LeadCard for a lead, or None.
    Separated from Telethon so it is fully testable.

    `force` builds a card even when the keywords say no — used for order-feed
    channels, where the LLM stage makes the actual call.
    """
    result = match(text)
    if not result.matched and not force:
        return None
    if result.anti and force:
        # Ads and self-promo stay rejected even in an order feed.
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
