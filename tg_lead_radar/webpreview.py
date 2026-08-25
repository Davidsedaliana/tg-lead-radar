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
    published: str = ""  # ISO datetime from the preview, if present

    @property
    def link(self) -> str:
        return f"https://t.me/{self.channel}/{self.msg_id}"


class _PreviewParser(HTMLParser):
    """Collects text and publish time of every .tgme_widget_message[data-post]."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.posts: list[Post] = []
        self._div_depth = 0
        # current widget state
        self._widget: dict | None = None
        self._widget_depth = 0
        self._text_parts: list[str] | None = None
        self._text_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = dict(attrs)
        if tag == "br":
            if self._text_parts is not None:
                self._text_parts.append("\n")
            return
        if tag == "time":
            if self._widget is not None and attr.get("datetime"):
                self._widget["published"] = attr["datetime"]
            return
        if tag != "div":
            return
        self._div_depth += 1
        classes = (attr.get("class") or "").split()
        data_post = attr.get("data-post") or ""
        if "tgme_widget_message" in classes and data_post:
            channel, _, raw_id = data_post.partition("/")
            if raw_id.isdigit():
                self._widget = {
                    "channel": channel, "msg_id": int(raw_id), "text": "", "published": ""
                }
            else:
                self._widget = None
            self._widget_depth = self._div_depth
            self._text_parts = None
        elif (
            self._widget is not None
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
        if self._widget is not None and self._div_depth == self._widget_depth:
            self._finish_widget()
        self._div_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._text_parts is not None:
            self._text_parts.append(data)

    def _finish_text(self) -> None:
        assert self._widget is not None and self._text_parts is not None
        raw = "".join(self._text_parts)
        lines = [re.sub(r"[ \t]+", " ", line).strip() for line in raw.split("\n")]
        self._widget["text"] = "\n".join(line for line in lines if line)
        self._text_parts = None

    def _finish_widget(self) -> None:
        assert self._widget is not None
        w = self._widget
        if w["text"]:
            self.posts.append(
                Post(
                    channel=w["channel"], msg_id=w["msg_id"],
                    text=w["text"], published=w["published"],
                )
            )
        self._widget = None


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
