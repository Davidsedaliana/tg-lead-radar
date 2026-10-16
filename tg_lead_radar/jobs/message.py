"""Telethon message -> plain data.

The full text with hidden links spelled out, every URL the post carries
(text, inline buttons, link preview), attachments and the forward origin.
Kept apart from the network code so it is testable on hand-built TL objects.
"""

import re
from dataclasses import dataclass
from datetime import datetime

from telethon.helpers import add_surrogate, del_surrogate
from telethon.tl import types

from ..card import message_link
from .text import find_urls, tidy_url, unique


@dataclass(frozen=True)
class Attachment:
    type: str  # photo | document | video | audio | voice | gif | sticker
    file_name: str | None = None
    mime_type: str | None = None
    size: int | None = None


@dataclass(frozen=True)
class Origin:
    """Where the vacancy really comes from: the channel a post was forwarded
    from, or the post a short repost links to (then `text` is that post's)."""

    kind: str  # "forward" | "link"
    chat_id: int | None
    username: str | None
    title: str | None
    msg_id: int | None
    posted_at: datetime | None
    text: str | None = None
    views: int | None = None

    @property
    def message_link(self) -> str | None:
        if self.msg_id is None or (self.chat_id is None and not self.username):
            return None
        return message_link(self.username, self.chat_id or 0, self.msg_id)


@dataclass(frozen=True)
class PostRef:
    """A t.me link to a post: by username, or t.me/c/<id> for private chats."""

    username: str | None
    chat_id: int | None
    msg_id: int


@dataclass(frozen=True)
class RawPost:
    chat_id: int
    msg_id: int
    username: str | None
    title: str
    text: str
    urls: tuple[str, ...] = ()
    buttons: tuple[tuple[str, str], ...] = ()  # (label, url) of inline buttons
    attachments: tuple[Attachment, ...] = ()
    posted_at: datetime | None = None
    views: int | None = None
    forward: Origin | None = None

    @property
    def message_link(self) -> str:
        return message_link(self.username, self.chat_id, self.msg_id)

    def post_refs(self) -> list[PostRef]:
        refs = []
        for url in self.urls:
            m = _POST_LINK_RE.match(url)
            if m:
                cid = int(m.group("cid")) if m.group("cid") else None
                refs.append(PostRef(m.group("user"), cid, int(m.group("msg"))))
        return refs


_POST_LINK_RE = re.compile(
    r"^https?://(?:t\.me|telegram\.me|telegram\.dog)/(?:s/)?"
    r"(?:c/(?P<cid>\d+)|(?P<user>[A-Za-z][A-Za-z0-9_]{3,31}))/(?P<msg>\d+)/?(?:\?.*)?$",
    re.IGNORECASE,
)


def _bare(url: str) -> str:
    url = re.sub(r"^[a-z]+://", "", url.strip().lower())
    return url.removeprefix("www.").rstrip("/")


def expand_text(text: str, entities) -> tuple[str, list[str]]:
    """Spell hidden links out: "Откликнуться" whose URL sits in an entity
    becomes "Откликнуться (https://…)", a hidden user mention gets its
    tg://user link. Returns the text and the entity URLs in order.

    Entity offsets count UTF-16 code units, hence the surrogate round trip:
    an emoji before the link would otherwise shift every position.
    """
    if not text:
        return "", []
    s = add_surrogate(text)
    urls: list[str] = []
    inserts: list[tuple[int, str]] = []
    for e in sorted(entities or (), key=lambda e: e.offset):
        end = e.offset + e.length
        label = del_surrogate(s[e.offset:end])
        if isinstance(e, types.MessageEntityTextUrl):
            urls.append(e.url)
            # A label that already is the URL needs no repeat.
            if "." not in label or not _bare(e.url).startswith(_bare(label)):
                inserts.append((end, f" ({e.url})"))
        elif isinstance(e, types.MessageEntityMentionName):
            url = f"tg://user?id={e.user_id}"
            urls.append(url)
            inserts.append((end, f" ({url})"))
        elif isinstance(e, types.MessageEntityUrl):
            urls.append(tidy_url(label))
    for end, insert in sorted(inserts, key=lambda x: x[0], reverse=True):
        s = s[:end] + insert + s[end:]
    return del_surrogate(s), urls


def button_links(markup) -> list[tuple[str, str]]:
    """URL buttons ("Откликнуться" → hh.ru) — channels often keep the apply
    link only there. Handles both the old KeyboardButtonUrl and the newer
    layer where the payload sits in button.type."""
    out = []
    for row in getattr(markup, "rows", None) or ():
        for button in getattr(row, "buttons", None) or ():
            payload = getattr(button, "type", None) or button
            url = getattr(payload, "url", None) or getattr(button, "url", None)
            user_id = getattr(payload, "user_id", None) or getattr(button, "user_id", None)
            label = getattr(button, "text", "") or ""
            if url:
                out.append((label, url))
            elif user_id:
                out.append((label, f"tg://user?id={user_id}"))
    return out


def attachments_of(media) -> list[Attachment]:
    """What is attached, never the file itself."""
    if isinstance(media, types.MessageMediaPhoto) and media.photo:
        return [Attachment("photo")]
    if isinstance(media, types.MessageMediaDocument) and isinstance(
        media.document, types.Document
    ):
        doc = media.document
        kind, name = "document", None
        for attr in doc.attributes:
            if isinstance(attr, types.DocumentAttributeFilename):
                name = attr.file_name
            elif isinstance(attr, types.DocumentAttributeVideo):
                kind = "video"
            elif isinstance(attr, types.DocumentAttributeAudio):
                kind = "voice" if attr.voice else "audio"
            elif isinstance(attr, types.DocumentAttributeAnimated):
                kind = "gif"
            elif isinstance(attr, types.DocumentAttributeSticker):
                kind = "sticker"
        return [Attachment(kind, name, doc.mime_type, doc.size)]
    return []


def _webpage_url(media) -> str | None:
    if isinstance(media, types.MessageMediaWebPage):
        return getattr(media.webpage, "url", None)
    return None


def forward_origin(message) -> Origin | None:
    fwd = getattr(message, "fwd_from", None)
    if fwd is None:
        return None
    # Telethon resolves the source chat when it came with the response.
    chat = getattr(getattr(message, "forward", None), "chat", None)
    return Origin(
        kind="forward",
        chat_id=getattr(fwd.from_id, "channel_id", None),
        username=getattr(chat, "username", None),
        title=getattr(chat, "title", None) or fwd.from_name,
        msg_id=fwd.channel_post,
        posted_at=fwd.date,
    )


def build_raw_post(message, chat, album=()) -> RawPost:
    """`album` holds the other messages of the same media group: the caption
    sits on one of them, the files are spread over all."""
    text, entity_urls = expand_text(getattr(message, "message", None) or "", message.entities)
    buttons = button_links(message.reply_markup)
    webpage = _webpage_url(message.media)
    urls = unique(
        entity_urls + find_urls(text) + [url for _, url in buttons] + ([webpage] if webpage else [])
    )
    attachments = attachments_of(message.media)
    for other in album:
        if other.id != message.id:
            attachments += attachments_of(other.media)
    return RawPost(
        chat_id=chat.id,
        msg_id=message.id,
        username=getattr(chat, "username", None),
        title=getattr(chat, "title", None) or getattr(chat, "username", None) or str(chat.id),
        text=text,
        urls=tuple(urls),
        buttons=tuple(buttons),
        attachments=tuple(attachments),
        posted_at=message.date,
        views=getattr(message, "views", None),
        forward=forward_origin(message),
    )


def as_link_origin(post: RawPost) -> Origin:
    return Origin(
        kind="link",
        chat_id=post.chat_id,
        username=post.username,
        title=post.title,
        msg_id=post.msg_id,
        posted_at=post.posted_at,
        text=post.text,
        views=post.views,
    )
