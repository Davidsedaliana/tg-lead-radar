import re
from dataclasses import dataclass, field

# Stems are matched at word start (\b + stem + optional word tail), so "бот"
# matches "бота"/"ботов" but not "работа". Multi-word stems allow any
# whitespace between words.
CATEGORIES: dict[str, list[str]] = {
    # priority order matters: the first matched category picks the reply draft
    "fix": [
        "почин", "исправ", "доработ", "доделать", "ошибк", "баг",
        "не работает", "перестал работа", "падает", "сломал",
    ],
    # NB: no standalone "telegram"/"телеграм" — SMM posts ("ведение
    # телеграм-канала") and contact lines ("Телеграм: @hr") ruin precision.
    "bot": [
        "бот", "чат-бот", "чатбот", "aiogram", "tg-бот",
        "телеграм-бот", "telegram-бот", "telegram bot", "телеграм бот",
    ],
    "ai": [
        "распознаван", "whisper", "транскриб", "расшифров", "нейросет",
        "gpt", "llm", "ии-", "оцифров",
    ],
    "automation": [
        "автоматизац", "автоматизир", "скрипт", "парсер", "парсинг", "спарсить",
        "интеграц", "excel", "таблиц", "google sheets", "гугл таблиц",
        "отчёт", "отчет", "crm", "амо", "выгрузк", "рутин",
    ],
    # Weak signals: alone they pass, but a veto topic (design/marketing post
    # that merely mentions them) knocks them out.
    "python": ["python", "питон", "джанго", "django", "fastapi", "api"],
}

WEAK_CATEGORIES = {"python"}

# Posts by other freelancers advertising themselves, CVs, etc.
ANTI = [
    "ищу работу", "ищу заказ", "ищу проект", "резюме", "#резюме",
    "предлагаю услуги", "мои услуги", "возьму заказ", "выполню ваш",
    "готов взяться", "открыт к предложени", "#ищуработу",
]

# Ads: promo posts, courses, giveaways — the channels' bread, our noise.
ADS = [
    "erid", "#реклама", "промокод", "скидк", "розыгрыш", "вебинар",
    "бесплатный урок", "бесплатный курс", "интенсив", "марафон",
    "подписывайся", "подпишись", "запишись на курс", "набор на курс",
    "регистрируйся", "по ссылке в шапке",
]

# Off-profile topics: if such a post matched only weak keywords, drop it.
TOPIC_VETO = [
    "дизайнер", "логотип", "smm", "смм", "таргетолог", "таргетированн",
    "копирайт", "монтаж", "видеомонтаж", "маркетолог", "продажник",
    "менеджер по продаж", "озвучк", "иллюстратор", "моушн", "3d-модел",
]

# Frontend/site-building vacancies are off-profile even when they mention
# integrations or APIs. Only a core match (bot/fix/ai) overrides this.
FRONTEND_VETO = [
    "react", "vue", "angular", "фронтенд", "frontend", "вёрстк", "верстк",
    "лендинг", "figma", "ui/ux", "веб-дизайн",
]

# Employment type: only freelance / project / one-off work is wanted.
# A staff-vacancy marker kills the post unless a freelance marker is present.
STAFF_MARKERS = [
    "в штат", "полная занятость", "по тк", "трудоустройств", "оклад",
    "фуллтайм", "фулл-тайм", "full-time", "fulltime", "испытательный срок",
    "соцпакет", "дмс", "график 5/2",
]
FREELANCE_MARKERS = [
    "фриланс", "проектн", "разов", "сдельн", "за задачу", "за проект",
    "подработк", "почасов", "бюджет",
]


def _stem_pattern(stem: str) -> re.Pattern[str]:
    words = [re.escape(w) for w in stem.split()]
    return re.compile(r"\b" + r"\s+".join(words) + r"\w*", re.IGNORECASE)

_CATEGORY_PATTERNS = {
    cat: [_stem_pattern(s) for s in stems] for cat, stems in CATEGORIES.items()
}
_ANTI_PATTERNS = [_stem_pattern(s) for s in ANTI + ADS]
_VETO_PATTERNS = [_stem_pattern(s) for s in TOPIC_VETO]
_FRONTEND_PATTERNS = [_stem_pattern(s) for s in FRONTEND_VETO]
_STAFF_PATTERNS = [_stem_pattern(s) for s in STAFF_MARKERS]
_FREELANCE_PATTERNS = [_stem_pattern(s) for s in FREELANCE_MARKERS]

# Categories whose match overrides the frontend veto.
CORE_CATEGORIES = {"fix", "bot", "ai"}


@dataclass(frozen=True)
class MatchResult:
    # Ordered by dominance: most keyword hits first, category priority breaks ties.
    categories: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    cat_hits: dict[str, int] = field(default_factory=dict)
    anti: bool = False

    @property
    def matched(self) -> bool:
        return bool(self.categories) and not self.anti


# Digest channels (Хабр Фриланс) post batches titled
# "Подборка заказов в категории ...". The relevant signal is the category
# in the title — the body always mentions everything.
_DIGEST_MARKER = "подборка заказов"


def match(text: str) -> MatchResult:
    if not text:
        return MatchResult()
    for pattern in _ANTI_PATTERNS:
        if pattern.search(text):
            return MatchResult(anti=True)
    first_line = text.split("\n", 1)[0]
    scan = first_line if _DIGEST_MARKER in first_line.lower() else text
    keywords: list[str] = []
    cat_hits: dict[str, int] = {}
    for cat, patterns in _CATEGORY_PATTERNS.items():
        hits = 0
        for p in patterns:
            m = p.search(scan)
            if m:
                hits += 1
                keywords.append(m.group(0).lower())
        if hits:
            cat_hits[cat] = hits
    order = list(CATEGORIES)
    categories = sorted(cat_hits, key=lambda c: (-cat_hits[c], order.index(c)))

    strong = [c for c in categories if c not in WEAK_CATEGORIES]
    if not strong and any(p.search(scan) for p in _VETO_PATTERNS):
        # A design/marketing/etc. post that merely mentions Python or an API.
        return MatchResult(anti=True)
    if not (set(categories) & CORE_CATEGORIES) and any(
        p.search(scan) for p in _FRONTEND_PATTERNS
    ):
        # Frontend/site vacancy that happens to mention integrations/APIs.
        return MatchResult(anti=True)
    if (
        categories
        and any(p.search(text) for p in _STAFF_PATTERNS)
        and not any(p.search(text) for p in _FREELANCE_PATTERNS)
    ):
        # Staff employment, not freelance/project work.
        return MatchResult(anti=True)
    return MatchResult(categories=categories, keywords=keywords, cat_hits=cat_hits)
