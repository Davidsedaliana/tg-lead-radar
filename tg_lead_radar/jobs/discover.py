"""Finding new vacancy channels: candidates from global search and Telegram's
"similar channels", scored by how alive and how QA-heavy they are.

Read-only by design: nothing is joined or subscribed to. The output is a
report, and subscribing is the owner's decision.
"""

import asyncio
import csv
import io
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from telethon.errors import FloodWaitError
from telethon.tl import types
from telethon.tl.functions.channels import (
    GetChannelRecommendationsRequest,
    GetFullChannelRequest,
)
from telethon.tl.functions.contacts import SearchRequest

from .config import DiscoverConfig, JobsConfig, Thresholds
from .export import atomic_write
from .extract import extract_apply_urls, extract_salary
from .filters import match_job
from .lists import list_items
from .message import button_links, expand_text
from .text import find_urls, strip_links

logger = logging.getLogger(__name__)

ALIVE, DOUBTFUL, DEAD = "живой", "сомнительный", "мертвяк"
REPORT_NAME = "channels_report"


class StopDiscovery(Exception):
    """Telegram asked for a longer pause than allowed: stop and report."""


class Paced:
    """Every request goes through here: a pause before each one, FloodWait
    waited out once if it is short, otherwise the run stops."""

    def __init__(self, client, cfg: DiscoverConfig, sleep=asyncio.sleep):
        self.client = client
        self.cfg = cfg
        self.sleep = sleep

    async def request(self, request):
        return await self._run(lambda: self.client(request))

    async def messages(self, entity, limit: int):
        return await self._run(lambda: self.client.get_messages(entity, limit=limit))

    async def _run(self, make_call):
        await self.sleep(self.cfg.pause_seconds)
        try:
            return await make_call()
        except FloodWaitError as exc:
            if exc.seconds > self.cfg.max_flood_wait:
                raise _stop(exc) from exc
            logger.warning("Flood wait %ss, waiting it out", exc.seconds)
            await self.sleep(exc.seconds + 1)
        try:
            return await make_call()
        except FloodWaitError as exc:  # a second wait in a row: back off for good
            raise _stop(exc) from exc


def _stop(exc: FloodWaitError) -> StopDiscovery:
    return StopDiscovery(f"Telegram просит паузу {exc.seconds} с — остановлено, отчёт неполный")


@dataclass
class Candidate:
    entity: object
    found_via: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ChannelStats:
    channel_id: int
    username: str | None
    title: str
    subscribers: int | None
    posts_sampled: int
    days_since_last: float | None
    posts_7d: int
    avg_views: float | None
    views_per_sub: float | None
    vacancy_share: float  # posts that are QA vacancies, not just about testing
    ad_share: float
    repost_share: float
    junk_share: float  # ads or reposts
    found_via: tuple[str, ...] = ()

    @property
    def link(self) -> str | None:
        return f"https://t.me/{self.username}" if self.username else None


@dataclass
class DiscoveryResult:
    stats: list[ChannelStats]
    candidates_total: int
    stopped: str | None = None


async def subscribed_ids(client) -> set[int]:
    """Every chat the account is in, archived ones included."""
    return {dialog.entity.id async for dialog in client.iter_dialogs()}


def _is_candidate(chat, cfg: DiscoverConfig, skip: set[int]) -> bool:
    if not isinstance(chat, types.Channel) or not chat.username or chat.id in skip:
        return False
    if chat.left is False and not chat.min:
        return False  # already a member
    return bool(chat.broadcast or (cfg.include_groups and chat.megagroup))


async def gather_candidates(
    call: Paced,
    cfg: DiscoverConfig,
    sources: list,
    skip: set[int],
    into: dict[int, Candidate],
) -> None:
    """Fills `into` as it goes, so a stopped run keeps what it found."""

    def add(chats, via: str) -> None:
        for chat in chats:
            if _is_candidate(chat, cfg, skip):
                into.setdefault(chat.id, Candidate(chat)).found_via.append(via)

    for query in cfg.queries:
        found = await call.request(SearchRequest(q=query, limit=cfg.search_limit))
        add(found.chats, f"поиск «{query}»")
    for source in sources:
        if not getattr(source, "broadcast", False):
            continue  # recommendations exist for channels only
        similar = await call.request(GetChannelRecommendationsRequest(channel=source))
        add(similar.chats, f"похож на @{source.username or source.title}")


def rank(candidates: dict[int, Candidate], limit: int) -> list[Candidate]:
    """Found by several queries or recommended by several channels first."""
    return sorted(
        candidates.values(),
        key=lambda c: (-len(c.found_via), -(getattr(c.entity, "participants_count", 0) or 0)),
    )[:limit]


def post_text(message) -> str:
    """The text the collector would see: hidden links spelled out, URL buttons
    appended. "Описание вакансии на GeekJob.ru" hides its only link."""
    text, _ = expand_text(getattr(message, "message", None) or "", message.entities)
    buttons = [url for _, url in button_links(getattr(message, "reply_markup", None))]
    return "\n".join([text, *buttons]) if buttons else text


def is_vacancy_post(text: str, config: JobsConfig) -> bool:
    """A QA vacancy rather than a post about testing: besides passing the QA
    filter it has a salary, an apply link or a vacancy word — or it is a
    digest with a QA line."""
    if not text:
        return False
    items = list_items(text)
    if items and any(config.filter.stems.any(strip_links(i.line)) for i in items):
        return True
    if not match_job(text, config.filter).matched:
        return False
    if config.discover.vacancy_markers.any(text):
        return True
    salary = extract_salary(text, config.extract)
    if salary.salary_from is not None or salary.salary_to is not None:
        return True
    return bool(extract_apply_urls(find_urls(text), config.extract))


def compute_stats(
    entity, messages, subscribers: int | None, now: datetime, config: JobsConfig,
    found_via=(),
) -> ChannelStats:
    posts = [m for m in messages if getattr(m, "action", None) is None and m.date]
    n = len(posts)
    texts = [post_text(m) for m in posts]
    last = max((m.date for m in posts), default=None)
    views = [m.views for m in posts if getattr(m, "views", None) is not None]
    avg_views = sum(views) / len(views) if views else None
    is_ad = [config.discover.ad_markers.any(t) for t in texts]
    is_repost = [getattr(m, "fwd_from", None) is not None for m in posts]
    vacancies = sum(is_vacancy_post(t, config) for t in texts)

    def share(count: int) -> float:
        return count / n if n else 0.0

    return ChannelStats(
        channel_id=entity.id,
        username=getattr(entity, "username", None),
        title=getattr(entity, "title", None) or "",
        subscribers=subscribers,
        posts_sampled=n,
        days_since_last=(now - last).total_seconds() / 86400 if last else None,
        posts_7d=sum(m.date >= now - timedelta(days=7) for m in posts),
        avg_views=avg_views,
        views_per_sub=avg_views / subscribers if avg_views is not None and subscribers else None,
        vacancy_share=share(vacancies),
        ad_share=share(sum(is_ad)),
        repost_share=share(sum(is_repost)),
        junk_share=share(sum(a or r for a, r in zip(is_ad, is_repost, strict=True))),
        found_via=tuple(found_via),
    )


def score(s: ChannelStats, cfg: DiscoverConfig) -> float:
    """0..100: freshness, activity, vacancy share and engagement, minus junk."""
    w, t = cfg.score, cfg.thresholds
    if s.days_since_last is None:
        return 0.0
    if s.days_since_last <= t.alive_max_days:
        fresh = 1.0
    else:
        span = max(t.dead_min_days - t.alive_max_days, 1)
        fresh = max(0.0, 1 - (s.days_since_last - t.alive_max_days) / span)
    activity = min(s.posts_7d / w.activity_target_posts_week, 1.0)
    vacancies = min(s.vacancy_share / w.vacancy_target_share, 1.0)
    engagement = min((s.views_per_sub or 0.0) / w.engagement_target, 1.0)
    base = (
        w.freshness * fresh + w.activity * activity + w.vacancies * vacancies
        + w.engagement * engagement
    )
    return round(100 * base * (1 - w.junk_penalty * s.junk_share), 1)


def label(s: ChannelStats, t: Thresholds) -> str:
    if s.days_since_last is None or s.days_since_last >= t.dead_min_days:
        return DEAD
    if (
        s.days_since_last <= t.alive_max_days
        and s.posts_7d >= t.alive_min_posts_week
        and s.vacancy_share >= t.alive_min_vacancy_share
    ):
        return ALIVE
    return DOUBTFUL


async def analyse(call: Paced, candidate: Candidate, config: JobsConfig, now: datetime):
    entity = candidate.entity
    messages = await call.messages(entity, config.discover.posts_per_channel)
    subscribers = getattr(entity, "participants_count", None)
    if not subscribers:
        full = await call.request(GetFullChannelRequest(entity))
        subscribers = getattr(full.full_chat, "participants_count", None)
    return compute_stats(entity, messages, subscribers, now, config, candidate.found_via)


async def discover(
    client,
    config: JobsConfig,
    sources: list,
    subscribed: set[int],
    sleep=asyncio.sleep,
    now: datetime | None = None,
) -> DiscoveryResult:
    cfg = config.discover
    call = Paced(client, cfg, sleep)
    now = now or datetime.now(UTC)
    skip = subscribed | {s.id for s in sources}
    candidates: dict[int, Candidate] = {}
    stopped = None
    try:
        await gather_candidates(call, cfg, sources, skip, candidates)
    except StopDiscovery as exc:
        stopped = str(exc)
    stats = []
    if not stopped:
        for candidate in rank(candidates, cfg.max_candidates):
            try:
                stats.append(await analyse(call, candidate, config, now))
            except StopDiscovery as exc:
                stopped = str(exc)
                break
            except Exception:
                name = getattr(candidate.entity, "username", candidate.entity.id)
                logger.exception("Cannot analyse @%s, skipped", name)
    return DiscoveryResult(stats=stats, candidates_total=len(candidates), stopped=stopped)


# --- report ------------------------------------------------------------------

COLUMNS = (
    ("score", "Скор"), ("status", "Статус"), ("link", "Ссылка"), ("title", "Канал"),
    ("subscribers", "Подписчики"), ("days_since_last", "Дней с поста"),
    ("posts_7d", "Постов за 7 дн."), ("avg_views", "Ср. просмотры"),
    ("views_per_sub", "Просмотры/подписчики"), ("vacancy_share", "Доля вакансий"),
    ("ad_share", "Доля рекламы"), ("repost_share", "Доля репостов"),
    ("posts_sampled", "Постов в выборке"), ("found_via", "Где найден"),
)


def _round(value: float | None, digits: int):
    if value is None:
        return None
    return round(value) if digits == 0 else round(value, digits)


def report_rows(stats: list[ChannelStats], cfg: DiscoverConfig) -> list[dict]:
    rows = []
    for s in stats:
        rows.append({
            "score": score(s, cfg),
            "status": label(s, cfg.thresholds),
            "link": s.link,
            "title": s.title,
            "subscribers": s.subscribers,
            "days_since_last": _round(s.days_since_last, 1),
            "posts_7d": s.posts_7d,
            "avg_views": _round(s.avg_views, 0),
            "views_per_sub": _round(s.views_per_sub, 3),
            "vacancy_share": round(s.vacancy_share, 3),
            "ad_share": round(s.ad_share, 3),
            "repost_share": round(s.repost_share, 3),
            "posts_sampled": s.posts_sampled,
            "found_via": "; ".join(s.found_via),
        })
    return sorted(rows, key=lambda r: -r["score"])


def _cell(value) -> str:
    if value is None:
        return "—"
    return str(value).replace("|", "/").replace("\n", " ")


def _pct(value: float) -> str:
    return f"{value * 100:.0f}%"


def render_report_md(rows: list[dict], result: DiscoveryResult, now: datetime) -> str:
    counts = {st: sum(r["status"] == st for r in rows) for st in (ALIVE, DOUBTFUL, DEAD)}
    lines = [
        "# Новые каналы с вакансиями QA",
        "",
        f"{now.strftime('%d.%m.%Y %H:%M')} UTC · кандидатов найдено: {result.candidates_total}, "
        f"проверено: {len(rows)} · {ALIVE}: {counts[ALIVE]}, {DOUBTFUL}: {counts[DOUBTFUL]}, "
        f"{DEAD}: {counts[DEAD]}",
        "",
        "Подписки не оформлялись: выберите каналы и подпишитесь сами.",
    ]
    if result.stopped:
        lines += ["", f"> ⚠️ {result.stopped}"]
    lines += [
        "",
        "| # | Канал | Статус | Скор | Подписчики | Дней с поста | Постов/7д | "
        "Ср. просмотры | Просм./подп. | Вакансии | Реклама | Репосты | Где найден |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for i, r in enumerate(rows, 1):
        channel = f"[{_cell(r['title'])}]({r['link']})" if r["link"] else _cell(r["title"])
        lines.append("| " + " | ".join([
            str(i), channel, r["status"], f"{r['score']:.1f}", _cell(r["subscribers"]),
            _cell(r["days_since_last"]), str(r["posts_7d"]), _cell(r["avg_views"]),
            _cell(r["views_per_sub"]), _pct(r["vacancy_share"]), _pct(r["ad_share"]),
            _pct(r["repost_share"]), _cell(r["found_via"]),
        ]) + " |")
    return "\n".join(lines) + "\n"


def render_report_csv(rows: list[dict]) -> str:
    """Comma-separated; the file gets a BOM so Excel opens Cyrillic right."""
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow([title for _, title in COLUMNS])
    for r in rows:
        writer.writerow(["" if r[key] is None else r[key] for key, _ in COLUMNS])
    return buf.getvalue()


def write_reports(
    result: DiscoveryResult, cfg: DiscoverConfig, out_dir: str | Path, now: datetime | None = None
) -> tuple[Path, Path]:
    now = now or datetime.now(UTC)
    rows = report_rows(result.stats, cfg)
    out = Path(out_dir)
    md, table = out / f"{REPORT_NAME}.md", out / f"{REPORT_NAME}.csv"
    atomic_write(md, render_report_md(rows, result, now))
    atomic_write(table, "﻿" + render_report_csv(rows))
    return md, table
