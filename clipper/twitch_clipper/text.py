"""Everything about the clip caption: chat sample, category guess, formatting."""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

CATEGORIES = {
    "funny": "😂 смешно",
    "hype": "🔥 хайп",
    "fail": "💀 фейл",
    "skill": "🎯 скилл",
    "drama": "😱 драма",
    "other": "✨ момент",
}

_KEYWORDS = {
    "funny": re.compile(r"а?х[аеы]х|пх[аеы]|хд+\b|лол|ору+\b|kekw|lul|lmao|xd+\b|😂|🤣", re.I),
    "hype": re.compile(r"pog|вау|w{3,}|ебать|нифига|имба|🔥|gg\b|гг\b|красав", re.I),
    "fail": re.compile(r"\bf{1,}\b|\bф{2,}\b|rip|рип|минус|фейл|слил|позор", re.I),
    "drama": re.compile(r"чего|что\?|\?{3,}|monkas|жесть|треш|омг|omg", re.I),
}

CAPTION_LIMIT = 1024  # Telegram's limit for a media caption
MESSAGE_LIMIT = 90


def guess_category(messages: Iterable[str]) -> str:
    counts: Counter[str] = Counter()
    for text in messages:
        for name, pattern in _KEYWORDS.items():
            if pattern.search(text):
                counts[name] += 1
    if not counts:
        return "other"
    return counts.most_common(1)[0][0]


def sample_chat(messages: Iterable[str], limit: int = 5) -> list[str]:
    """The most repeated messages of the moment, one line each."""
    counts: Counter[str] = Counter()
    original: dict[str, str] = {}
    for text in messages:
        text = " ".join(text.split())
        if not text:
            continue
        key = text.lower()
        counts[key] += 1
        original.setdefault(key, text)
    order = {key: i for i, key in enumerate(original)}
    top = sorted(counts, key=lambda k: (-counts[k], order[k]))[:limit]
    top.sort(key=lambda k: order[k])
    return [_cut(original[k], MESSAGE_LIMIT) for k in top]


def _cut(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def build_caption(
    streamer: str,
    ts: float,
    timezone: str,
    category: str,
    chat_mult: float,
    audio_mult: float | None,
    score: int | None,
    summary: str,
    chat_lines: list[str],
) -> str:
    try:
        zone = ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError):
        zone = None  # no time zone database (Windows without tzdata): use the computer's time
    when = datetime.fromtimestamp(ts, zone).strftime("%d.%m %H:%M")
    head = [f"🎬 {streamer}", when, CATEGORIES.get(category, CATEGORIES["other"])]
    head.append(f"чат x{chat_mult:.1f}")
    if audio_mult is not None:
        head.append(f"🔊 x{audio_mult:.1f}")
    lines = [" · ".join(head)]
    if score is not None:
        lines.append(f"🤖 {score}/10: {summary}" if summary else f"🤖 {score}/10")
    lines += [f"💬 {m}" for m in chat_lines]
    caption = "\n".join(lines)
    while len(caption) > CAPTION_LIMIT and len(lines) > 1:
        lines.pop()
        caption = "\n".join(lines)
    return caption[:CAPTION_LIMIT]
