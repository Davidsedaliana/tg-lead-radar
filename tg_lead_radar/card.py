import html
from dataclasses import dataclass

EXCERPT_LIMIT = 600

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

    @property
    def emoji(self) -> str:
        for cat in self.categories:
            if cat in CATEGORY_EMOJI:
                return CATEGORY_EMOJI[cat]
        return "🎯"

    @property
    def tags(self) -> str:
        return " ".join(f"#{c}" for c in self.categories)


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
    return (
        f"{card.emoji} {card.channel_title} · {card.tags}\n"
        f"{card.link}\n\n"
        f"{excerpt(card.text)}\n\n"
        f"— — — черновик отклика — — —\n\n"
        f"{card.draft}"
    )


def render_html(card: LeadCard) -> str:
    """Web mode: Bot API HTML with collapsible quotes."""
    title = html.escape(card.channel_title)
    return (
        f'{card.emoji} <b><a href="{card.link}">{title}</a></b> · {card.tags}\n'
        f"<blockquote expandable>{html.escape(excerpt(card.text))}</blockquote>\n"
        f"✍️ <b>Черновик отклика</b>\n"
        f"<blockquote expandable>{html.escape(card.draft)}</blockquote>"
    )
