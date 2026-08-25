"""Web mode: no my.telegram.org credentials needed.

Polls t.me/s previews of public channels, filters posts, and notifies via a
regular Bot API bot (the bot must have been /start-ed by the recipient once).
On the very first poll of a channel only the freshest few matches are sent,
so the backlog does not flood the chat.
"""

import logging
import os
import time
from dataclasses import replace
from pathlib import Path
from typing import IO

import httpx

from .card import LeadCard, age_hours, render_html
from .config import Settings, load_settings
from .llm import judge_lead
from .radar import build_card
from .store import SeenStore
from .webpreview import fetch_channel

logger = logging.getLogger(__name__)

# First poll of a channel: its whole preview is new. Send what is still
# actionable rather than a fixed few — an order from two days ago is worth
# an answer, one from June is not.
FIRST_POLL_MAX_AGE_HOURS = 48
FIRST_POLL_MAX_CARDS = 10
STARS = {1: "⭐", 2: "⭐⭐", 3: "⭐⭐⭐"}


def trim_backlog(cards: list[LeadCard]) -> list[LeadCard]:
    fresh = [c for c in cards if (age_hours(c.published) or 0) <= FIRST_POLL_MAX_AGE_HOURS]
    return fresh[-FIRST_POLL_MAX_CARDS:]


def acquire_single_instance_lock(path: str) -> IO[bytes]:
    """Autostart plus a manual run would otherwise notify about every lead
    twice. The lock is held by the OS on an open file, so it disappears with
    the process — no stale lock file survives a crash. (A loopback port would
    be simpler, but Hyper-V/Docker reserve wide port ranges on Windows and
    binding inside them fails.)"""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    # Deliberately not a context manager: the handle must outlive this call —
    # closing it would release the lock immediately.
    handle = open(path, "a+b")  # noqa: SIM115
    try:
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        handle.close()
        raise SystemExit("Радар уже запущен — второй экземпляр не нужен") from exc
    return handle


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
        # In an order feed every post is a real order, so the keyword gate only
        # loses leads phrased in words our stems miss — let the model judge all.
        judge_all = settings.llm_enabled and channel in settings.order_feed_list()
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
                force=judge_all,
            )
            store.mark_seen(channel, post.msg_id, card.categories if card else [])
            if not card:
                continue
            if store.is_text_seen(post.text):
                logger.info("Duplicate text in @%s/%s — skipped", channel, post.msg_id)
                continue
            store.mark_text_seen(post.text)
            cards.append(card)

        if first_time:
            trimmed = trim_backlog(cards)
            if len(trimmed) != len(cards):
                logger.info("@%s: first poll, keeping %d of %d (fresh only)",
                            channel, len(trimmed), len(cards))
            cards = trimmed

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
    lock = acquire_single_instance_lock(str(Path(settings.db_path).with_name("radar.lock")))
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
            lock.close()


if __name__ == "__main__":
    run()
