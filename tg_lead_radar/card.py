EXCERPT_LIMIT = 600


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


def format_card(
    channel_title: str, link: str, text: str, categories: list[str], draft: str
) -> str:
    tags = " ".join(f"#{c}" for c in categories)
    return (
        f"🎯 {channel_title} · {tags}\n"
        f"{link}\n\n"
        f"{excerpt(text)}\n\n"
        f"— — — черновик отклика — — —\n\n"
        f"{draft}"
    )
