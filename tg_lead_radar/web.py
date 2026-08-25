"""Web mode: no my.telegram.org credentials needed.

Polls t.me/s previews of public channels, filters posts, and notifies via a
regular Bot API bot (the bot must have been /start-ed by the recipient once).
On the very first poll of a channel only the freshest few matches are sent,
so the backlog does not flood the chat.
"""

import logging
import time
from dataclasses import replace

import httpx

from .card import LeadCard, render_html
from .config import Settings, load_settings
from .llm import judge_lead
from .radar import build_card
from .store import SeenStore
from .webpreview import fetch_channel

logger = logging.getLogger(__name__)

BACKLOG_NOTIFY_LIMIT = 3
STARS = {1: "⭐", 2: "⭐⭐", 3: "⭐⭐⭐"}


def apply_llm(client: httpx.Client, settings: Settings, card: LeadCard) -> LeadCard | None:
    """Precision stage: returns an enriched card, or None if the model says
    this is not a lead. Model unavailable -> card passes through unchanged."""
    verdict = judge_lead(client, settings.ollama_url, settings.ollama_model, card.text)
    if verdict is None:
        return card
    if not verdict.relevant:
        logger.info("LLM rejected %s: %s", card.link, verdict.reason or "not relevant")
        return None
    return replace(
        card,
        draft=verdict.draft or card.draft,
        reason=verdict.reason,
        fit=STARS.get(verdict.fit, card.fit),
    )


def send_via_bot(client: httpx.Client, token: str, user_id: int, card: LeadCard) -> None:
    resp = client.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={
            "chat_id": user_id,
            "text": render_html(card),
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
            "reply_markup": {
                "inline_keyboard": [[{"text": "Открыть пост ↗", "url": card.link}]]
            },
        },
    )
    resp.raise_for_status()


def poll_once(client: httpx.Client, store: SeenStore, settings: Settings) -> int:
    """One sweep over all web channels. Returns how many cards were sent."""
    sent = 0
    for channel in settings.web_channel_list():
        try:
            posts = fetch_channel(client, channel)
        except Exception:
            logger.exception("Failed to fetch @%s", channel)
            continue
        if not posts:
            logger.warning("@%s: no posts in preview (chat instead of channel?)", channel)
            continue

        first_time = not store.has_chat(channel)
        cards: list[LeadCard] = []
        for post in sorted(posts, key=lambda p: p.msg_id):
            if store.is_seen(channel, post.msg_id):
                continue
            card = build_card(
                text=post.text,
                chat_id=0,
                msg_id=post.msg_id,
                chat_title=f"@{channel}",
                chat_username=channel,
                published=post.published,
            )
            store.mark_seen(channel, post.msg_id, card.categories if card else [])
            if not card:
                continue
            if store.is_text_seen(post.text):
                logger.info("Duplicate text in @%s/%s — skipped", channel, post.msg_id)
                continue
            store.mark_text_seen(post.text)
            cards.append(card)

        if first_time and len(cards) > BACKLOG_NOTIFY_LIMIT:
            logger.info("@%s: first poll, trimming backlog %d -> %d",
                        channel, len(cards), BACKLOG_NOTIFY_LIMIT)
            cards = cards[-BACKLOG_NOTIFY_LIMIT:]

        for card in cards:
            if settings.llm_enabled:
                judged = apply_llm(client, settings, card)
                if judged is None:
                    continue
                card = judged
            try:
                send_via_bot(client, settings.bot_token, settings.notify_user_id, card)
                sent += 1
            except Exception:
                logger.exception("Failed to notify about a lead in @%s", channel)
    return sent


def run() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # httpx logs full request URLs — for Bot API that includes the token.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    settings = load_settings()
    if not settings.bot_token or not settings.notify_user_id:
        raise SystemExit("Web mode needs BOT_TOKEN and NOTIFY_USER_ID in .env")
    store = SeenStore(settings.db_path)
    logger.info(
        "Web radar up: %s, every %ss", settings.web_channel_list(), settings.poll_interval_seconds
    )
    with httpx.Client(timeout=30) as client:
        try:
            while True:
                sent = poll_once(client, store, settings)
                if sent:
                    logger.info("Poll cycle done: %d cards sent", sent)
                time.sleep(settings.poll_interval_seconds)
        except KeyboardInterrupt:
            logger.info("Stopped")
        finally:
            store.close()


if __name__ == "__main__":
    run()
