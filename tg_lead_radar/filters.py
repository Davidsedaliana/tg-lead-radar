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
    "bot": [
        "бот", "чат-бот", "чатбот", "telegram", "телеграм", "aiogram", "tg-бот",
    ],
    "ai": [
        "распознаван", "whisper", "транскриб", "расшифров", "нейросет",
        "gpt", "llm", "ии-", "оцифров",
    ],
    "automation": [
        "автоматизац", "автоматизир", "скрипт", "парсер", "парсинг", "спарсить",
        "интеграц", "api", "excel", "таблиц", "google sheets", "гугл таблиц",
        "отчёт", "отчет", "crm", "амо", "выгрузк", "рутин",
    ],
    "python": ["python", "питон", "джанго", "django", "fastapi"],
}

# Posts by other freelancers advertising themselves, CVs, etc.
ANTI = [
    "ищу работу", "ищу заказ", "ищу проект", "резюме", "#резюме",
    "предлагаю услуги", "мои услуги", "возьму заказ", "выполню ваш",
    "готов взяться", "открыт к предложени", "#ищуработу",
]


def _stem_pattern(stem: str) -> re.Pattern[str]:
    words = [re.escape(w) for w in stem.split()]
    return re.compile(r"\b" + r"\s+".join(words) + r"\w*", re.IGNORECASE)

_CATEGORY_PATTERNS = {
    cat: [_stem_pattern(s) for s in stems] for cat, stems in CATEGORIES.items()
}
_ANTI_PATTERNS = [_stem_pattern(s) for s in ANTI]


@dataclass(frozen=True)
class MatchResult:
    categories: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    anti: bool = False

    @property
    def matched(self) -> bool:
        return bool(self.categories) and not self.anti


def match(text: str) -> MatchResult:
    if not text:
        return MatchResult()
    for pattern in _ANTI_PATTERNS:
        if pattern.search(text):
            return MatchResult(anti=True)
    categories: list[str] = []
    keywords: list[str] = []
    for cat, patterns in _CATEGORY_PATTERNS.items():
        for p in patterns:
            m = p.search(text)
            if m:
                if cat not in categories:
                    categories.append(cat)
                keywords.append(m.group(0).lower())
    return MatchResult(categories=categories, keywords=keywords)
