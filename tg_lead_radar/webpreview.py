"""Reading public channels WITHOUT any Telegram credentials.

Every public channel has an HTML preview at https://t.me/s/<username> with
the ~20 latest posts. This module fetches and parses it (stdlib html.parser,
no heavy dependencies). Chats/supergroups have no such preview — they need
the userbot mode.
"""

import re
from dataclasses import dataclass
from html.parser import HTMLParser

import httpx

_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) tg-lead-radar"}


@dataclass(frozen=True)
class Post:
    channel: str
    msg_id: int
    text: str

    @property
    def link(self) -> str:
        return f"https://t.me/{self.channel}/{self.msg_id}"


class _PreviewParser(HTMLParser):
    """Collects text of .tgme_widget_message_text inside .tgme_widget_message[data-post]."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.posts: list[Post] = []
        self._div_depth = 0
        self._post: tuple[str, int] | None = None
        self._post_depth = 0
        self._text_parts: list[str] | None = None
        self._text_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "br":
            if self._text_parts is not None:
                self._text_parts.append("\n")
            return
        if tag != "div":
            return
        self._div_depth += 1
        attr = dict(attrs)
        classes = (attr.get("class") or "").split()
        data_post = attr.get("data-post") or ""
        if "tgme_widget_message" in classes and data_post:
            channel, _, raw_id = data_post.partition("/")
            # A new widget always resets the context, valid id or not —
            # otherwise its text would be attributed to the previous post.
            self._post = (channel, int(raw_id)) if raw_id.isdigit() else None
            self._post_depth = self._div_depth
        elif (
            self._post is not None
            and "tgme_widget_message_text" in classes
            and self._text_parts is None
        ):
            self._text_parts = []
            self._text_depth = self._div_depth

    def handle_endtag(self, tag: str) -> None:
        if tag != "div":
            return
        if self._text_parts is not None and self._div_depth == self._text_depth:
            self._finish_text()
        if self._post is not None and self._div_depth == self._post_depth:
            self._post = None
        self._div_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._text_parts is not None:
            self._text_parts.append(data)

    def _finish_text(self) -> None:
        assert self._post is not None and self._text_parts is not None
        raw = "".join(self._text_parts)
        lines = [re.sub(r"[ \t]+", " ", line).strip() for line in raw.split("\n")]
        text = "\n".join(line for line in lines if line)
        channel, msg_id = self._post
        if text:
            self.posts.append(Post(channel=channel, msg_id=msg_id, text=text))
        self._text_parts = None
        self._post = None


def parse_preview(html: str) -> list[Post]:
    parser = _PreviewParser()
    parser.feed(html)
    return parser.posts


def fetch_channel(client: httpx.Client, username: str) -> list[Post]:
    resp = client.get(f"https://t.me/s/{username}", headers=_HEADERS, follow_redirects=True)
    resp.raise_for_status()
    # A channel without a preview (or a chat) redirects to t.me/<name> —
    # that page has no message widgets, so we just return [].
    return parse_preview(resp.text)
