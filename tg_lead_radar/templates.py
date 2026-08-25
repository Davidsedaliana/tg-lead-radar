"""Reply drafts per category. The first line is deliberately left with a
placeholder — the whole point is to rewrite it for the client's task, the
draft only saves typing the rest."""

DRAFTS: dict[str, str] = {
    "bot": (
        "Здравствуйте! Сделаю бота, который [СУТЬ ЗАДАЧИ СВОИМИ СЛОВАМИ]. "
        "Важный момент в таких задачах — чтобы заявки/данные не терялись при сбоях; "
        "у меня это решается очередью с повторной отправкой.\n\n"
        "Готовый пример такой связки с кодом и тестами: "
        "https://github.com/Davidsedaliana/lead-intake-bot\n\n"
        "Уточните, [ВОПРОС ПО ДЕЛУ] — назову срок и цену."
    ),
    "fix": (
        "Здравствуйте! Похоже на [ГИПОТЕЗА О ПРИЧИНЕ]. Берусь найти причину и починить — "
        "8 лет работаю в автоматизации тестирования, разбор чужого кода — моя ежедневная работа. "
        "После починки оставлю тест, который не даст ошибке вернуться молча.\n\n"
        "Пришлите [код / текст ошибки / как воспроизвести] — скажу точную оценку.\n\n"
        "Как я оформляю код: https://github.com/Davidsedaliana"
    ),
    "ai": (
        "Здравствуйте! Задачу [СУТЬ] могу сделать в том числе локально — распознавание "
        "речи/документов прямо на вашем железе, если данные нельзя отправлять во внешние "
        "сервисы. Собрал и выложил работающее приложение локальной диктовки на GPU: "
        "https://github.com/Davidsedaliana/voicetype-local\n\n"
        "Уточните, [ОБЪЁМ / ФОРМАТ ДАННЫХ] — назову срок и цену."
    ),
    "automation": (
        "Здравствуйте! Задачу вида «[СУТЬ]» автоматизирую скриптом, который запускается "
        "одной кнопкой или по расписанию. Работаю с тестами и обработкой ошибок: если входные "
        "данные кривые — вы получите понятное сообщение, а не испорченный результат.\n\n"
        "Примеры кода: https://github.com/Davidsedaliana\n\n"
        "Уточните, [ОТКУДА ДАННЫЕ И В КАКОМ ВИДЕ НУЖЕН РЕЗУЛЬТАТ] — назову срок и цену."
    ),
    "python": (
        "Здравствуйте! Возьмусь: Python — мой основной стек (боты, автоматизация, интеграции), "
        "8 лет в автоматизации тестирования в крупном финтехе.\n\n"
        "Портфолио с кодом и тестами: https://github.com/Davidsedaliana\n\n"
        "Уточните, [ВОПРОС ПО ДЕЛУ] — назову срок и цену."
    ),
}

# Fallback order; normally `categories` already comes dominance-ordered
# (most keyword hits first) from filters.match.
PRIORITY = ["fix", "bot", "ai", "automation", "python"]


def pick_draft(categories: list[str]) -> str:
    for cat in categories:
        if cat in DRAFTS:
            return DRAFTS[cat]
    for cat in PRIORITY:
        if cat in categories:
            return DRAFTS[cat]
    return DRAFTS["python"]
