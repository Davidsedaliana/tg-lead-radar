"""Jobs mode polling: read the vacancy folder through the account and keep QA
vacancies as raw data.

The first sight of a channel loads JOBS_BACKFILL_DAYS of history; after that
only posts newer than the last one read (min_id), so a busy channel cannot
overflow between two polls the way a fixed "last 25" window would.
Read-only: the one thing ever sent is the optional card to Saved Messages.
"""

import asyncio
import logging
import re
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import asdict
from datetime import UTC, datetime, timedelta

from telethon.errors import FloodWaitError
from telethon.tl import types
from telethon.tl.functions.messages import GetDialogFiltersRequest

from ..config import Settings
from .config import JobsConfig
from .dedup import fingerprint, normalize, same_vacancy
from .export import render_card, write_digests
from .extract import extract_fields, extract_title
from .filters import match_job
from .lists import ListItem, item_context, list_items
from .message import Origin, RawPost, as_link_origin, build_raw_post
from .store import JobsStore, Source, to_iso
from .telegraph import Article, article_path, fetch_article
from .text import find_urls, strip_links, unique

logger = logging.getLogger(__name__)


def _folder_key(title: str) -> str:
    """"💼 Вакансии" and "вакансии" name the same folder."""
    return re.sub(r"[\W_]+", "", title).casefold()


async def folder_peers(client, folder: str) -> list | None:
    """Chats of the Telegram folder named `folder`, or None if there is none."""
    result = await client(GetDialogFiltersRequest())
    for f in getattr(result, "filters", result):
        # Newer layers wrap folder titles in TextWithEntities.
        title = getattr(getattr(f, "title", None), "text", getattr(f, "title", None))
        if not isinstance(title, str) or _folder_key(title) != _folder_key(folder):
            continue
        peers = list(getattr(f, "pinned_peers", None) or [])
        peers += list(getattr(f, "include_peers", None) or [])
        if not peers and (getattr(f, "broadcasts", False) or getattr(f, "groups", False)):
            logger.warning(
                "Folder %r is defined by rules (all channels/groups); "
                "only chats added to it by hand are read",
                folder,
            )
        return peers
    return None


async def resolve_jobs_sources(client, settings: Settings) -> list:
    """Channels and groups of the folder plus JOBS_CHANNELS, without duplicates."""
    targets: list = []
    if settings.jobs_folder:
        try:
            peers = await folder_peers(client, settings.jobs_folder)
        except Exception:
            logger.exception("Cannot read Telegram folders")
            peers = None
        if peers is None:
            logger.warning("Folder %r not found, using JOBS_CHANNELS only", settings.jobs_folder)
        targets += peers or []
    targets += settings.jobs_channel_list()

    entities, seen = [], set()
    for target in targets:
        try:
            entity = await client.get_entity(target)
        except Exception:
            logger.exception("Cannot resolve %s, skipped", target)
            continue
        if isinstance(entity, types.User) or entity.id in seen:
            continue  # people and bots in the folder are not vacancy feeds
        seen.add(entity.id)
        entities.append(entity)
    return entities


def _origin_dict(origin: Origin | None) -> dict | None:
    if origin is None:
        return None
    return {
        "kind": origin.kind,
        "channel": origin.username,
        "channel_title": origin.title,
        "message_link": origin.message_link,
        "posted_at": to_iso(origin.posted_at),
        "text": origin.text,
    }


def _article_origin(article: Article) -> Origin:
    return Origin(
        kind="telegraph", chat_id=None, username=None, title=article.title, msg_id=None,
        posted_at=None, text=article.text, url=article.url,
    )


class JobsRadar:
    def __init__(
        self,
        client,
        store: JobsStore,
        settings: Settings,
        config: JobsConfig,
        known_handles: Iterable[str] = (),
        fetch_article=fetch_article,
    ):
        self.client = client
        self.store = store
        self.settings = settings
        self.config = config
        self.fetch_article = fetch_article
        self.entities: list = []
        # Handles of channels we read are never an employer's contact.
        self._known = {h.lower() for h in known_handles}
        self._refreshed_at: datetime | None = None

    async def refresh_sources(self, force: bool = False) -> None:
        """Re-read the folder now and then: a channel added to it is picked
        up without a restart."""
        now = datetime.now(UTC)
        every = timedelta(minutes=self.config.collector.folder_refresh_minutes)
        if not force and self._refreshed_at and now - self._refreshed_at < every:
            return
        self._refreshed_at = now
        entities = await resolve_jobs_sources(self.client, self.settings)
        if not entities and self.entities:
            logger.warning("Jobs: no sources resolved, keeping the previous list")
            return
        old = {e.id for e in self.entities}
        for e in entities:
            if e.id not in old:
                logger.info("Jobs: watching %s (@%s)", getattr(e, "title", e.id),
                            getattr(e, "username", None) or e.id)
        self.entities = entities
        self._known |= {e.username.lower() for e in entities if getattr(e, "username", None)}

    async def poll(self) -> int:
        """One pass over all sources. Returns the number of new vacancies."""
        await self.refresh_sources()
        new_total, days = 0, set()
        for entity in self.entities:
            try:
                new, touched = await self.poll_channel(entity)
            except FloodWaitError as exc:
                logger.warning("Flood wait %ss on %s, pausing", exc.seconds, entity.id)
                await asyncio.sleep(exc.seconds + 1)
                continue
            except Exception:
                logger.exception("Jobs: failed to read %s", getattr(entity, "username", entity.id))
                continue
            new_total += new
            days |= touched
            await asyncio.sleep(1)  # be gentle with the API between sources
        if days:
            write_digests(self.store, self.settings.jobs_export_dir, days)
        return new_total

    async def poll_channel(self, entity) -> tuple[int, set[str]]:
        last = self.store.last_msg_id(entity.id)
        backfill = last is None
        messages, newest = await self._fetch(entity, last)

        albums: dict[int, list] = defaultdict(list)
        for m in messages:
            if m.grouped_id:
                albums[m.grouped_id].append(m)

        new, days = 0, set()
        for m in sorted(messages, key=lambda m: m.id):
            if not getattr(m, "message", None):
                continue  # service message, or a file of an album without the caption
            post = build_raw_post(m, entity, albums.get(m.grouped_id, ()))
            for vacancy_id, is_new in await self.process(post):
                day = self.store.posted_date(vacancy_id)
                if day:
                    days.add(day)
                if is_new:
                    new += 1
                    if self.settings.jobs_notify and not backfill:
                        await self._notify(vacancy_id)

        title = getattr(entity, "title", None) or str(entity.id)
        self.store.set_last_msg_id(
            entity.id, getattr(entity, "username", None), title, max(newest, last or 0)
        )
        if backfill:
            logger.info("Jobs: %s first read, %d posts over %d days, %d vacancies",
                        title, len(messages), self.settings.jobs_backfill_days, new)
        return new, days

    async def _fetch(self, entity, last: int | None) -> tuple[list, int]:
        """New messages and the id of the newest one seen. The newest id is
        kept even when it is older than the backfill window, so a quiet
        channel is not read from the very beginning next time."""
        if last is not None:
            messages = [m async for m in self.client.iter_messages(entity, min_id=last)]
            return messages, max((m.id for m in messages), default=last)
        since = datetime.now(UTC) - timedelta(days=self.settings.jobs_backfill_days)
        messages, newest = [], 0
        limit = self.config.collector.backfill_max_posts
        async for m in self.client.iter_messages(entity, limit=limit):
            newest = max(newest, m.id)
            if m.date and m.date < since:
                break
            messages.append(m)
        return messages, newest

    async def process(self, post: RawPost) -> list[tuple[str, bool]]:
        """Store a post if it is a QA vacancy — or, for a list post, each of its
        QA lines. Returns (vacancy id, is new) for every vacancy stored."""
        if self.store.has_post(post.chat_id, post.msg_id):
            return []
        origin = post.forward
        linked = None if origin else await self._linked_original(post)
        if linked:
            origin = as_link_origin(linked)
        scan = post.text if linked is None else f"{post.text}\n\n{linked.text}"

        items = list_items(scan)
        if items:
            stored = await self._process_list(post, origin, scan, items)
            if stored:
                return stored
            # No QA line in it: still judge the post as a whole below.

        verdict = match_job(scan, self.config.filter)
        if not verdict.matched:
            if verdict.rejected_by:
                logger.debug("Jobs: %s rejected (%s)", post.message_link, verdict.rejected_by)
            return []
        # A repost by link is just a wrapper: the original is the vacancy.
        body = linked.text if linked else post.text
        urls = unique(list(post.urls) + list(linked.urls if linked else ()))
        title = None
        article = None if origin else await self._single_article(urls)
        if article:
            origin, title = _article_origin(article), article.title
            scan, body = f"{scan}\n\n{article.text}", article.text
            urls = unique(urls + find_urls(article.text))
        return [self._store(
            post, origin,
            text=post.text,
            scan=scan,
            body=body,
            urls=urls,
            buttons=list(post.buttons) + list(linked.buttons if linked else ()),
            attachments=list(post.attachments) + list(linked.attachments if linked else ()),
            stems=verdict.stems,
            title=title,
        )]

    async def _process_list(self, post: RawPost, origin, scan: str, items: list[ListItem]):
        """"Подборка вакансий": every line with a QA title becomes a vacancy of
        its own, with the post's shared lines as context. A line that leads to
        a telegra.ph article gets the article as its full text."""
        rejected_by = match_job(scan, self.config.filter).rejected_by or ""
        if rejected_by.startswith(("anti:", "anti_title:")):
            return []  # an ad or a list of courses
        context = item_context(scan, items)
        stored = []
        for item in items:
            if not self.config.filter.stems.any(strip_links(item.line)):
                continue
            text = f"{item.line}\n\n{context}" if context else item.line
            verdict = match_job(text, self.config.filter)
            if not verdict.matched:
                continue
            item_scan, body, urls, item_origin = text, text, [item.url], origin
            article = await self._article(item.url)
            if article:
                item_origin = _article_origin(article)
                item_scan, body = f"{text}\n\n{article.text}", article.text
                urls = unique(urls + find_urls(article.text))
            stored.append(self._store(
                post, item_origin, text=text, scan=item_scan, body=body, urls=urls,
                buttons=[], attachments=[], stems=verdict.stems,
                # the digest line often carries the salary: "QA Lead (от 3000$)"
                item=(item.index, len(items)), title=item.title,
            ))
        return stored

    async def _article(self, url: str) -> Article | None:
        if not article_path(url):
            return None
        article = await self.fetch_article(url)
        await asyncio.sleep(0.3)  # a digest can hold dozens of articles
        return article

    async def _single_article(self, urls: list[str]) -> Article | None:
        """A post that names one telegra.ph article keeps the vacancy there."""
        articles = [u for u in urls if article_path(u)]
        return await self._article(articles[0]) if len(articles) == 1 else None

    def _store(
        self, post: RawPost, origin: Origin | None, *, text: str, scan: str, body: str,
        urls: list[str], buttons: list, attachments: list, stems: list[str],
        item: tuple[int, int] | None = None, title: str | None = None,
    ) -> tuple[str, bool]:
        exclude = self._known | {
            h.lower() for h in (post.username, origin.username if origin else None) if h
        }
        fields = extract_fields(scan, urls, buttons, self.config.extract, exclude)
        fp = fingerprint(body, fields, self.config.dedup)
        sources = self._sources(post, origin, item[0] if item else 0)

        duplicate = self._find_duplicate(fp, post, fields)
        if duplicate:
            self.store.attach(duplicate, fields, stems, sources)
            logger.info("Jobs: %s is a copy of %s", post.message_link, duplicate)
            return duplicate, False

        vacancy_id = fp.text_hash[:16]
        record = {
            "text": text,
            "title": title or extract_title(body, self.config.dedup.generic_titles),
            **fields,
            "links": urls,
            "attachments": [asdict(a) for a in attachments],
            "original": _origin_dict(origin),
            "list_item": {"index": item[0], "of": item[1]} if item else None,
            "matched_stems": stems,
        }
        self.store.add_vacancy(vacancy_id, fp, normalize(body), record, sources)
        logger.info("Jobs: new vacancy %s from %s", vacancy_id, post.message_link)
        return vacancy_id, True

    def _find_duplicate(self, fp, post: RawPost, fields: dict) -> str | None:
        """The same vacancy link, the same text, or text close enough."""
        found = self.store.find_by_url(fields.get("apply_urls"))
        if found:
            return found
        found = self.store.find_by_hash(fp.text_hash)
        if found:
            return found
        when = post.posted_at or datetime.now(UTC)
        since = to_iso(when - timedelta(days=self.config.dedup.window_days))
        for vacancy_id, other in self.store.candidates(since):
            if same_vacancy(fp, other, self.config.dedup):
                return vacancy_id
        return None

    @staticmethod
    def _sources(post: RawPost, origin: Origin | None, item: int = 0) -> list[Source]:
        sources = [Source(
            chat_id=post.chat_id, msg_id=post.msg_id, kind="post", channel=post.username,
            channel_title=post.title, message_link=post.message_link,
            posted_at=to_iso(post.posted_at), views=post.views, item=item,
        )]
        if origin and origin.chat_id is not None and origin.msg_id is not None:
            sources.append(Source(
                chat_id=origin.chat_id, msg_id=origin.msg_id, kind=origin.kind,
                channel=origin.username, channel_title=origin.title,
                message_link=origin.message_link, posted_at=to_iso(origin.posted_at),
                views=origin.views,
            ))
        return sources

    async def _linked_original(self, post: RawPost) -> RawPost | None:
        """A short post that links to exactly one other post is a repost by
        link ("Новая вакансия: t.me/x/123"): fetch the post it points to."""
        refs = post.post_refs()
        if len(refs) != 1:
            return None
        if len(strip_links(post.text).strip()) > self.config.collector.linked_post_max_chars:
            return None
        ref = refs[0]
        try:
            chat = await self.client.get_entity(ref.username or types.PeerChannel(ref.chat_id))
            message = await self.client.get_messages(chat, ids=ref.msg_id)
        except FloodWaitError:
            raise
        except Exception as exc:
            logger.info("Jobs: cannot fetch the original of %s: %s", post.message_link, exc)
            return None
        if not message or not getattr(message, "message", None):
            return None
        return build_raw_post(message, chat)

    async def _notify(self, vacancy_id: str) -> None:
        record = self.store.record(vacancy_id)
        if record is None:
            return
        try:
            await self.client.send_message(
                self.settings.notify_chat, render_card(record), link_preview=False
            )
        except Exception:
            logger.exception("Jobs: failed to send the card for %s", vacancy_id)
