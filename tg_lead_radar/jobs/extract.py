"""Vacancy fields without a model: regexes and dictionaries from jobs.toml.

Every field is optional and a doubt gives None, never a guess: the external
agent reads the full text anyway, and a wrong field misleads it more than an
empty one.
"""

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from .config import ExtractConfig
from .text import find_emails, find_urls, is_hashtag_line, strip_links, unique

# --- salary -----------------------------------------------------------------

_LETTER = r"[^\W\d_]"
_CURRENCIES = (
    # order matters: "бел. руб" before "руб", "usdt" before "usd"
    ("BYN", r"byn|бел\.?\s?руб\w*"),
    ("RUB", r"₽|руб\w*\.?|rub|rur|р\.|р(?![^\W\d_])"),
    ("USDT", r"usdt"),
    ("USD", r"\$|usd|долл\w*|бакс\w*"),
    ("EUR", r"€|eur|евро"),
    ("GBP", r"£|gbp"),
    ("KZT", r"₸|kzt|тенге"),
    ("UAH", r"₴|uah|грн\.?"),
    ("UZS", r"uzs|сум(?![^\W\d_])"),
    ("AMD", r"֏|amd|драм\w*"),
    ("GEL", r"₾|gel|лари"),
    ("RSD", r"rsd|динар\w*"),
    ("AED", r"aed|дирхам\w*"),
)
_CURRENCY = rf"(?<!{_LETTER})(?:" + "|".join(
    f"(?P<{code}>{pattern})" for code, pattern in _CURRENCIES
) + rf")(?!{_LETTER})"
_CURRENCY_RE = re.compile(_CURRENCY, re.IGNORECASE)
_CURRENCY_HEAD_RE = re.compile(r"\s*" + _CURRENCY, re.IGNORECASE)
_CURRENCY_TAIL_RE = re.compile(_CURRENCY + r"\s*$", re.IGNORECASE)
# Two currencies with stronger value: amounts in them get a stricter sanity range.
_HARD_CURRENCIES = {"USD", "EUR", "GBP", "USDT"}

_AMOUNT_RE = re.compile(
    r"(?<![\w.,])"
    r"(?P<num>\d{1,3}(?:[    .,']\d{3})+(?!\d)|\d+(?:[.,]\d{1,2})?(?!\d))"
    rf"(?:\s?(?P<mult>тыс\w*\.?|т\.\s?р\.?|млн\w*\.?|(?:к|k|тр)(?!{_LETTER})))?",
    re.IGNORECASE,
)
# A number followed by these is a count, not money: "от 3 лет", "200+ человек".
_UNIT_RE = re.compile(
    r"\+?\s*(?:лет|год\w*|years?|мес\w*|months?|дн\w*|days?|недел\w*|weeks?|"
    r"час\w*|hours?|чел\w*|сотрудник\w*|специалист\w*|people|шт\w*|раза?(?!\w)|"
    r"%|x(?!\w)|мин\w*|сек\w*|км|km|кв\w*|этаж\w*|проект\w*|заказ\w*|подписчик\w*)",
    re.IGNORECASE,
)
_RANGE_JOIN_RE = re.compile(r"\s*(?:[-–—−]|\.\.\.?|до|to|по)\s*", re.IGNORECASE)
# "от $3000", "до USD 4000" — a currency may sit between the word and the number.
_CURRENCY_GAP = r"(?!\w)\s*(?:[$€£₽₸]|usd|eur|rub)?\s*$"
_FROM_RE = re.compile(r"(?<!\w)(?:от|from|starting|min\.?|не менее)" + _CURRENCY_GAP, re.I)
_TO_RE = re.compile(r"(?<!\w)(?:до|up to|to|max\.?|не более)" + _CURRENCY_GAP, re.I)
_PERIODS = (
    ("hour", r"/\s?ч(?:ас)?(?!\w)|(?:в|за|per|an|a)\s+(?:час|ч\.|hour)|/\s?h(?:ou)?r|hourly"),
    ("year", r"/\s?(?:год|year|yr)|(?:в|за|per|a)\s+(?:год|year|annum)|annually|yearly"),
    ("month", r"/\s?(?:мес|month|mo(?!\w))|(?:в|за|per|a)\s+(?:мес|month)|monthly|ежемесячн"),
)
_PERIOD_RE = re.compile("|".join(f"(?P<{n}>{p})" for n, p in _PERIODS), re.IGNORECASE)

# Plausible ranges, so "компенсация 5000 ₽" or a phone number is not a salary.
_SANE = {
    "month": {"RUB": (10_000, 5_000_000), "HARD": (300, 100_000)},
    "hour": {"RUB": (100, 20_000), "HARD": (5, 1_000)},
    "year": {"RUB": (120_000, 60_000_000), "HARD": (3_000, 2_000_000)},
}


@dataclass(frozen=True)
class Salary:
    salary_from: int | None = None
    salary_to: int | None = None
    currency: str | None = None
    salary_period: str | None = None  # "hour" | "month" | "year", only when stated


@dataclass(frozen=True)
class _Amount:
    value: float
    raw: float  # before the "к/тыс" multiplier
    factor: int
    start: int
    end: int  # includes a currency right after the number
    currency: str | None
    plus: bool  # "200к+"


def _parse_number(num: str) -> tuple[float, bool]:
    """Value and whether it has a decimal part ("1,5 млн")."""
    if re.fullmatch(r"\d+[.,]\d{1,2}", num):
        return float(num.replace(",", ".")), True
    return float(re.sub(r"\D", "", num)), False


def _factor(mult: str | None) -> int:
    if not mult:
        return 1
    return 1_000_000 if mult.lower().startswith("млн") else 1_000


def _currency_code(match: re.Match[str] | None) -> str | None:
    return match.lastgroup if match else None


def _amounts(line: str) -> list[_Amount]:
    out: list[_Amount] = []
    for m in _AMOUNT_RE.finditer(line):
        raw, has_decimals = _parse_number(m.group("num"))
        mult = m.group("mult")
        if has_decimals and not mult:
            continue  # a date or a version, not money
        rest = line[m.end():]
        if _UNIT_RE.match(rest):
            continue
        head = _CURRENCY_HEAD_RE.match(rest)
        if not head and not mult and re.match(_LETTER, rest):
            continue  # glued to a word: "3года"
        currency = _currency_code(head) or _currency_code(
            _CURRENCY_TAIL_RE.search(line[max(0, m.start() - 8):m.start()])
        )
        if mult and re.match(r"т\.?\s?р|тр", mult, re.IGNORECASE):
            currency = currency or "RUB"
        end = m.end() + (head.end() if head else 0)
        factor = _factor(mult)
        out.append(_Amount(
            value=raw * factor, raw=raw, factor=factor, start=m.start(), end=end,
            currency=currency, plus=line[end:].lstrip().startswith("+"),
        ))
    return out


def _expressions(line: str) -> list[list[_Amount]]:
    """Amounts grouped into ranges: "200–300к", "от 200 до 300 тыс"."""
    groups: list[list[_Amount]] = []
    for amount in _amounts(line):
        if groups and len(groups[-1]) == 1:
            between = line[groups[-1][-1].end:amount.start]
            # "from $5000 to $7000": the second number's currency sits in the gap
            between = _CURRENCY_TAIL_RE.sub("", between)
            if _RANGE_JOIN_RE.fullmatch(between):
                groups[-1].append(amount)
                continue
        groups.append([amount])
    return groups


def _free_currencies(text: str) -> set[str]:
    """Currencies named apart from any number: "Вилка (USD): 3000-4000",
    "оплата в USDT". Those attached to a number belong to that number."""
    free = set()
    for m in _CURRENCY_RE.finditer(text):
        if m.group(0).strip(". ").lower() == "р":
            continue  # a lone letter is too ambiguous to stand for roubles
        before = text[:m.start()].rstrip()
        after = text[m.end():].lstrip()
        if re.search(r"\d\s*(?:к|k|тыс\w*\.?|млн\w*\.?)?$", before, re.I) or re.match(r"\d", after):
            continue
        free.add(m.lastgroup)
    return free


def _sane(low: float | None, high: float | None, currency: str | None, period: str) -> bool:
    ranges = _SANE[period]
    if currency is None:
        lo = min(r[0] for r in ranges.values())
        hi = max(r[1] for r in ranges.values())
    elif currency == "RUB":
        lo, hi = ranges["RUB"]
    elif currency in _HARD_CURRENCIES:
        lo, hi = ranges["HARD"]
    else:
        lo, hi = 1, float("inf")
    values = [v for v in (low, high) if v is not None]
    if low is not None and high is not None and low > high:
        return False
    return all(lo <= v <= hi for v in values)


def _interpret(
    group: list[_Amount], line: str, salary_line: bool, free: set[str], cfg: ExtractConfig
) -> Salary | None:
    first, last = group[0], group[-1]
    window_end = min(len(line), last.end + 25)
    period_match = _PERIOD_RE.search(line, first.start, window_end)
    period = period_match.lastgroup if period_match else None

    if len(group) == 2:
        lo, hi = first.value, last.value
        # "200–300к": the multiplier written once applies to both ends
        if last.factor > 1 and first.factor == 1 and first.raw < 1000:
            lo = first.raw * last.factor
        elif first.factor > 1 and last.factor == 1 and last.raw < 1000:
            hi = last.raw * first.factor
    else:
        prefix = line[:first.start]
        if first.plus or _FROM_RE.search(prefix):
            lo, hi = first.value, None
        elif _TO_RE.search(prefix):
            lo, hi = None, first.value
        else:
            lo = hi = first.value

    attached = first.currency or last.currency
    if attached:
        options = [attached]
    elif len(free) == 1:
        options = [next(iter(free))]
    elif not free and cfg.default_currency:
        options = [cfg.default_currency, None]
    else:
        options = [None]

    # "до 250 на руки": bare hundreds in a salary line are thousands
    if salary_line and period != "hour" and options[0] in (None, "RUB"):
        if lo is not None and lo < 1000 and first.factor == 1:
            lo *= 1000
        if hi is not None and hi < 1000 and last.factor == 1:
            hi *= 1000

    for currency in options:
        if _sane(lo, hi, currency, period or "month"):
            return Salary(
                salary_from=int(lo) if lo is not None else None,
                salary_to=int(hi) if hi is not None else None,
                currency=currency,
                salary_period=period,
            )
    return None


def extract_salary(text: str, cfg: ExtractConfig) -> Salary:
    """The first plausible salary. Lines with a salary word come first and may
    hold bare numbers; other lines count only with a currency or "к/тыс"."""
    lines = [ln for ln in strip_links(text).split("\n") if ln.strip()]
    marked: list[str] = []
    other: list[str] = []
    carry = False  # "Зарплата:" on its own line marks the next one
    for line in lines:
        if cfg.salary_skip.any(line):
            carry = False
            continue
        if carry or cfg.salary_markers.any(line):
            marked.append(line)
            carry = not re.search(r"\d", line)
        else:
            other.append(line)
    free = _free_currencies(text)
    for line, salary_line in [(ln, True) for ln in marked] + [(ln, False) for ln in other]:
        for group in _expressions(line):
            explicit = any(a.currency or a.factor > 1 for a in group)
            if not salary_line and not explicit:
                continue
            salary = _interpret(group, line, salary_line, free, cfg)
            if salary:
                return salary
    return Salary()


# --- format, city, grade, stack -------------------------------------------


def extract_format(text: str, cfg: ExtractConfig) -> str | None:
    scan = strip_links(text)
    if cfg.hybrid.any(scan):
        return "hybrid"
    remote, office = cfg.remote.any(scan), cfg.office.any(scan)
    if remote != office:
        return "remote" if remote else "office"
    return None  # nothing said, or "офис или удалёнка" — not ours to decide


def extract_city(text: str, cfg: ExtractConfig) -> str | None:
    lines = [ln for ln in strip_links(text).split("\n") if not cfg.city_skip.any(ln)]
    found = cfg.cities.found("\n".join(lines))
    return found[0] if len(found) == 1 else None


def extract_grade(text: str, cfg: ExtractConfig) -> list[str] | None:
    """From the title area, hashtags and "Грейд:"-style lines only: the body
    says "будете менторить junior'ов" about someone else."""
    lines = [ln.strip() for ln in strip_links(text).split("\n") if ln.strip()]
    zone = lines[:3] + [
        ln for ln in lines[3:]
        if is_hashtag_line(ln) or cfg.grade_line_markers.any(" ".join(ln.split()[:2]))
    ]
    zone += re.findall(r"#\w+", "\n".join(lines[3:]))
    found = cfg.grades.found("\n".join(zone).replace("_", " "))
    return found or None


def extract_stack(text: str, cfg: ExtractConfig) -> list[str] | None:
    found = cfg.stack.found(strip_links(text).replace("_", " "))
    return found or None


# --- contacts and apply links -------------------------------------------------

_USERNAME_RE = re.compile(r"(?<![\w@./])@([A-Za-z][A-Za-z0-9_]{3,31})(?!\w)")
_TME_USER_RE = re.compile(
    r"^https?://(?:t\.me|telegram\.me|telegram\.dog)/([A-Za-z][A-Za-z0-9_]{3,31})/?(?:\?.*)?$",
    re.IGNORECASE,
)
_TELEGRAM_HOSTS = {"t.me", "telegram.me", "telegram.dog", "telegra.ph"}
_RESERVED_PATHS = {
    "joinchat", "addlist", "addstickers", "addemoji", "share", "proxy", "socks",
    "iv", "boost", "setlanguage", "login", "confirmphone",
}


def _contact_from_url(url: str) -> str | None:
    if url.lower().startswith("tg://user?id="):
        return url
    m = _TME_USER_RE.match(url)
    if m and m.group(1).lower() not in _RESERVED_PATHS:
        return "@" + m.group(1)
    return None


def extract_contacts(
    text: str,
    buttons: list[tuple[str, str]],
    cfg: ExtractConfig,
    exclude: set[str],
) -> list[str] | None:
    """@handles, emails, t.me/<person> and hidden user mentions. Lines that
    advertise the channel itself ("Подписывайтесь @…") are skipped, and so are
    `exclude` handles (the source channels)."""
    found: list[str] = []
    for line in text.split("\n"):
        if cfg.contact_skip.any(line):
            continue
        found += [f"@{u}" for u in _USERNAME_RE.findall(line)]
        found += find_emails(line)
        found += [c for c in map(_contact_from_url, find_urls(line)) if c]
    for label, url in buttons:
        if not cfg.contact_skip.any(label):
            contact = _contact_from_url(url)
            if contact:
                found.append(contact)
    skip = {name.lower().lstrip("@") for name in exclude if name}
    kept = [c for c in found if c.lstrip("@").lower() not in skip]
    return unique(kept) or None


def _domain_match(host: str, path: str, entry: str) -> bool:
    domain, _, prefix = entry.partition("/")
    if host != domain and not host.endswith("." + domain):
        return False
    return not prefix or path.lstrip("/").startswith(prefix)


def extract_apply_urls(urls: list[str], cfg: ExtractConfig) -> list[str] | None:
    """Job boards, ATS, forms and career pages. Telegram links never count:
    a channel called "qa_jobs" is not an application form."""
    out = []
    for url in urls:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower().removeprefix("www.")
        if not host or host in _TELEGRAM_HOSTS:
            continue
        path = parts.path.lower()
        tokens = host.split(".") + [seg for seg in path.split("/") if seg]
        if any(_domain_match(host, path, entry) for entry in cfg.apply_domains) or any(
            marker in token for token in tokens for marker in cfg.apply_path_markers
        ):
            out.append(url)
    return unique(out) or None


def extract_fields(
    text: str,
    urls: list[str],
    buttons: list[tuple[str, str]],
    cfg: ExtractConfig,
    exclude: set[str],
) -> dict:
    salary = extract_salary(text, cfg)
    return {
        "salary_from": salary.salary_from,
        "salary_to": salary.salary_to,
        "currency": salary.currency,
        "salary_period": salary.salary_period,
        "format": extract_format(text, cfg),
        "city": extract_city(text, cfg),
        "grade": extract_grade(text, cfg),
        "stack": extract_stack(text, cfg),
        "contacts": extract_contacts(text, buttons, cfg, exclude),
        "apply_urls": extract_apply_urls(urls, cfg),
    }
