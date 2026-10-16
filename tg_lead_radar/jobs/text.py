"""Text helpers shared by the filter, the extractor and dedup."""

import re

_STOP = r"[^\s<>()\[\]{}\"'«»]"
_URL_RE = re.compile(
    rf"(?:https?://|tg://|www\.){_STOP}+"
    rf"|(?<![\w@./])(?:t\.me|telegram\.me|telegram\.dog)/{_STOP}+",
    re.IGNORECASE,
)
# Bare domains ("hh.ru/vacancy/1"). Lowercase only, so "ASP.NET" or
# "Node.JS" in a stack line are not taken for sites.
_BARE_DOMAIN_RE = re.compile(
    r"(?<![\w@./-])(?:[a-z0-9-]+\.)+"
    r"(?:ru|com|io|co|me|org|net|dev|tech|kz|by|uz|so|gle|ai|app|de|eu|pro|work|jobs)"
    rf"(?:/{_STOP}*)?(?![\w.])"
)
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)*\.[A-Za-z]{2,}")
_MENTION_RE = re.compile(r"(?<![\w.])@\w+")
_TRAILING = ".,;:!?…»)"

_HASHTAG_LINE = re.compile(r"(?:#[\w_]+[\s,]*)+", re.UNICODE)


def _tidy(url: str) -> str:
    url = url.rstrip(_TRAILING)
    if not re.match(r"[a-z]+://", url, re.IGNORECASE):
        url = "https://" + url
    return url


def find_urls(text: str) -> list[str]:
    """Every URL in the text, with a scheme, in order of appearance."""
    found: list[tuple[int, str]] = []
    taken: list[tuple[int, int]] = []
    for m in _URL_RE.finditer(text):
        found.append((m.start(), _tidy(m.group(0))))
        taken.append(m.span())
    for m in _BARE_DOMAIN_RE.finditer(text):
        if any(a <= m.start() < b for a, b in taken) or _inside_email(text, m.start()):
            continue
        found.append((m.start(), _tidy(m.group(0))))
    return unique(url for _, url in sorted(found))


def _inside_email(text: str, pos: int) -> bool:
    return any(m.start() <= pos < m.end() for m in _EMAIL_RE.finditer(text))


def find_emails(text: str) -> list[str]:
    return unique(m.group(0).rstrip(".").lower() for m in _EMAIL_RE.finditer(text))


def strip_links(text: str) -> str:
    """Text without URLs, emails and @mentions: channel footers ("@qa_jobs")
    and link slugs must not count as words of the post."""
    text = _URL_RE.sub(" ", text)
    text = _EMAIL_RE.sub(" ", text)
    text = _BARE_DOMAIN_RE.sub(" ", text)
    return _MENTION_RE.sub(" ", text)


def is_hashtag_line(line: str) -> bool:
    return bool(_HASHTAG_LINE.fullmatch(line.strip()))


def headline_zone(text: str) -> str:
    """Leading hashtag lines, the first real line and the hashtag lines right
    after it. Vacancy posts often open with "#вакансия #qa" before the title."""
    zone: list[str] = []
    seen_title = False
    for line in (ln.strip() for ln in text.split("\n")):
        if not line:
            continue
        if is_hashtag_line(line):
            zone.append(line)
            continue
        if seen_title:
            break
        zone.append(line)
        seen_title = True
    return "\n".join(zone)


def unique(items) -> list:
    seen = set()
    out = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out
