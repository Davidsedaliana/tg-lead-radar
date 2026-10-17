"""Posts that list several vacancies, one per line with its own link.

"Подборка Junior/Стажёр", "🔹 Senior QA Engineer (link)", a weekly digest:
each such line is a vacancy of its own, and the rest of the post (a header
like "удалённо", a company description) is context shared by all of them.
"""

import re
from collections import Counter
from dataclasses import dataclass
from urllib.parse import urlsplit

from .text import find_urls, strip_urls

MIN_ITEMS = 3
_DIGEST_HEADER_RE = re.compile(
    r"подборк|дайджест|digest|ваканси\w*\s+(?:дня|недели)|top.?vacanc|vacancies of the week",
    re.IGNORECASE,
)
MAX_ITEM_CHARS = 200
# Items must outnumber the prose: three "Откликнуться (link)" lines under a
# long description are one vacancy with links, not a list.
PROSE_MIN_CHARS = 40

_TELEGRAM_HOSTS = {"t.me", "telegram.me", "telegram.dog"}
_JOB_PATH_MARKERS = ("job", "vacanc", "vakans", "career", "position", "opening")


@dataclass(frozen=True)
class ListItem:
    index: int  # 1-based position among the items of the post
    line: str
    title: str
    url: str


def _is_telegram(url: str) -> bool:
    return (urlsplit(url).hostname or "").lower() in _TELEGRAM_HOSTS


def _item_title(line: str) -> str:
    title = re.sub(r"\(\s*\)", "", strip_urls(line))
    title = re.sub(r"^[^\w(«\"@]+", "", " ".join(title.split()))
    return title.strip(" .,;:—–-")


def _host(url: str) -> str:
    return (urlsplit(url).hostname or "").lower().removeprefix("www.")


def list_items(text: str) -> list[ListItem] | None:
    """The vacancy lines of a list post, or None if the post is not a list."""
    items: list[ListItem] = []
    prose = 0
    for line in (ln.strip() for ln in text.split("\n")):
        if not line:
            continue
        urls = find_urls(line)
        external = [u for u in urls if not _is_telegram(u)]
        if len(external) == 1 and len(urls) <= 2 and len(line) <= MAX_ITEM_CHARS:
            title = _item_title(line)
            if title:
                items.append(ListItem(len(items) + 1, line, title, external[0]))
                continue
        if not urls and len(line) >= PROSE_MIN_CHARS:
            prose += 1
    if not items or len(items) <= prose:
        return None
    header = next((ln for ln in text.split("\n") if ln.strip()), "")
    if _DIGEST_HEADER_RE.search(header):
        return items  # "Подборка вакансий" says it plainly, even with one item
    # Without such a header: at least three items, leading to one board
    # (youngjunior.ru, personio…). "Откликнуться", "Сайт", "LinkedIn" of a
    # single vacancy lead to different sites.
    top_host = Counter(_host(i.url) for i in items).most_common(1)[0][1]
    if len(items) < MIN_ITEMS or top_host < 2 or top_host * 2 < len(items):
        return None
    return items


def item_context(text: str, items: list[ListItem]) -> str:
    """Lines shared by every item: headers and prose, without the other items,
    category headings ("Разработка:") and channel footers full of links."""
    item_lines = {item.line for item in items}
    kept = []
    for line in (ln.strip() for ln in text.split("\n")):
        if not line or line in item_lines:
            continue
        if any(_is_telegram(u) for u in find_urls(line)) or len(find_urls(line)) >= 2:
            continue
        if line.endswith(":") and len(line) <= 30:
            continue
        kept.append(line)
    return "\n".join(kept)


def vacancy_url_key(url: str) -> str | None:
    """A URL that names one vacancy (hh.ru/vacancy/123, …/job/senior-qa-3),
    normalised for comparison; None for links shared by many vacancies such
    as a careers page or a Telegram channel."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower().removeprefix("www.")
    if not host or host in _TELEGRAM_HOSTS:
        return None
    segments = [s for s in parts.path.lower().split("/") if s]
    if not segments:
        return None
    last = segments[-1]
    in_job_path = any(m in s for s in segments[:-1] for m in _JOB_PATH_MARKERS)
    if re.search(r"\d", last) or (in_job_path and len(last) >= 6):
        return f"{host}/{'/'.join(segments)}"
    return None
