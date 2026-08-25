import html
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

EXCERPT_LIMIT = 600

_MSK = timezone(timedelta(hours=3))
_BUDGET_RE = re.compile(r"вилка|бюджет|оплат|цена|стоимост|гонорар|₽|руб\b|\d{2,}\s?000", re.I)

CORE_FIT = {"fix", "bot", "ai"}


def extract_budget(text: str) -> str | None:
    """First line that talks money — shown as the card's budget hint."""
    for line in text.split("\n"):
        if _BUDGET_RE.search(line):
            line = line.strip()
            return line[:90]
    return None


def fit_stars(categories: list[str], cat_hits: dict[str, int]) -> str:
    if set(categories) & CORE_FIT:
        return "⭐⭐⭐"
    if cat_hits.get("automation", 0) >= 2:
        return "⭐⭐⭐"
    if "automation" in categories:
        return "⭐⭐"
    return "⭐"


def format_published(iso: str) -> str:
    if not iso:
        return ""
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        return ""
    return dt.astimezone(_MSK).strftime("%d.%m %H:%M мск")

CATEGORY_EMOJI = {
    "fix": "🔧",
    "bot": "🤖",
    "ai": "🧠",
    "automation": "⚙️",
    "python": "🐍",
}


@dataclass(frozen=True)
class LeadCard:
    channel_title: str
    link: str
    text: str
    categories: list[str]
    draft: str
    published: str = ""  # ISO datetime
    fit: str = ""  # ⭐-rating of profile fit
    reason: str = ""  # one-line "why this fits", filled by the LLM stage

    @property
    def emoji(self) -> str:
        for cat in self.categories:
            if cat in CATEGORY_EMOJI:
                return CATEGORY_EMOJI[cat]
        return "🎯"

    @property
    def tags(self) -> str:
        return " ".join(f"#{c}" for c in self.categories)

    @property
    def meta_line(self) -> str:
        parts = []
        published = format_published(self.published)
        if published:
            parts.append(f"🕐 {published}")
        if self.fit:
            parts.append(f"{self.fit} под профиль")
        return " · ".join(parts)

    @property
    def budget(self) -> str | None:
        return extract_budget(self.text)


def message_link(username: str | None, chat_id: int, msg_id: int) -> str:
    if username:
        return f"https://t.me/{username}/{msg_id}"
    # Private chats have no public link; t.me/c works for participants.
    internal = str(chat_id).removeprefix("-100")
    return f"https://t.me/c/{internal}/{msg_id}"


def excerpt(text: str, limit: int = EXCERPT_LIMIT) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def render_plain(card: LeadCard) -> str:
    """Userbot mode: plain text into Saved Messages."""
    lines = [f"{card.emoji} {card.channel_title} · {card.tags}"]
    if card.meta_line:
        lines.append(card.meta_line)
    if card.reason:
        lines.append(f"🎯 {card.reason}")
    if card.budget:
        lines.append(f"💰 {card.budget}")
    lines += [
        card.link,
        "",
        excerpt(card.text),
        "",
        "— — — черновик отклика — — —",
        "",
        card.draft,
    ]
    return "\n".join(lines)


def render_html(card: LeadCard) -> str:
    """Web mode: Bot API HTML with collapsible quotes."""
    title = html.escape(card.channel_title)
    lines = [f'{card.emoji} <b><a href="{card.link}">{title}</a></b> · {card.tags}']
    if card.meta_line:
        lines.append(card.meta_line)
    if card.reason:
        lines.append(f"🎯 <i>{html.escape(card.reason)}</i>")
    if card.budget:
        lines.append(f"💰 {html.escape(card.budget)}")
    lines += [
        f"<blockquote expandable>{html.escape(excerpt(card.text))}</blockquote>",
        "✍️ <b>Черновик отклика</b>",
        f"<blockquote expandable>{html.escape(card.draft)}</blockquote>",
    ]
    return "\n".join(lines)
