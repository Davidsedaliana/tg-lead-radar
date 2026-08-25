"""Optional local-LLM stage (Ollama): judge a lead and write its draft.

Keyword filters are the cheap recall stage — they let through anything that
smells relevant. This module is the precision stage: a local model decides
whether the post really is a freelance order matching the profile, explains
why in one line, and writes a personalised reply.

Any failure (model down, bad JSON, timeout) returns None and the caller keeps
the keyword verdict and the template draft. Nothing leaves the machine.
"""

import json
import logging
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

PROFILE = """Давид — Python-разработчик на фрилансе.

БЕРЁТ (relevant=true) любые задачи, где нужен код на бэкенде или работа с данными:
- Telegram-боты и mini apps: любые — заявки, магазины, опросы, сбор информации, \
рассылки, доработка чужого бота
- автоматизация рутины: скрипты, обработка файлов и Excel, отчёты, парсинг \
и сбор данных, интеграции между сервисами (CRM, amoCRM, Google Sheets, API)
- обработка данных и ИИ: распознавание речи, разбор документов, работа с \
моделями — в том числе локально на железе заказчика, когда нельзя в облако
- починка и доработка: чужой или сгенерированный код, ошибки, ускорение, тесты
- бэкенд на Python: сервисы, API, базы данных, десктоп-утилиты

НЕ БЕРЁТ (relevant=false):
- задачи без программирования: дизайн, SMM, маркетинг, тексты, монтаж, продажи
- фронтенд и вёрстку как таковую: React/Vue-разработка, лендинги, сайты «под ключ», \
мобильные приложения под iOS/Android
- работу в штат: полная занятость, оклад, трудоустройство по ТК
- рекламу, курсы, резюме и предложения услуг от других исполнителей

Портфолио: github.com/Davidsedaliana — lead-intake-bot (бот заявок с интеграцией \
в таблицу), voicetype-local (локальное распознавание речи на GPU)."""

JUDGE_PROMPT = f"""{PROFILE}

Тебе дают текст поста из Telegram-канала. Реши, подходящий ли это заказ.

Правило: если в задаче есть код, бот, скрипт, парсинг, интеграция, база данных, \
обработка файлов или данных — это ПОДХОДИТ, даже если предметная область \
незнакомая или бюджет маленький. Отказывай только по причинам из списка \
«НЕ БЕРЁТ». Сомневаешься — ставь relevant=true и fit=2.

Примеры:
- «Создание бота по сбору информации, 5000 ₽» → relevant=true, fit=3
- «Доработать mini app, ускорить работу» → relevant=true, fit=3
- «Выгрузка заказов из amoCRM в Google Таблицу» → relevant=true, fit=3
- «Разработать веб-сервис учёта склада на Python» → relevant=true, fit=3
- «Сверстать лендинг по макету Figma» → relevant=false
- «Ищем React-разработчика в штат, оклад 200к» → relevant=false
- «Нужен дизайнер для логотипа» → relevant=false

Отвечай СТРОГО одним JSON-объектом и заполняй поля по порядку — сначала \
разберись в задаче, только потом выноси вердикт:
{{
  "task": "что конкретно требуется сделать, одна фраза своими словами",
  "tech_work": true|false,
  "employment": "проект" | "штат" | "не заказ",
  "relevant": true|false,
  "fit": 1|2|3,
  "reason": "одна строка до 90 символов: что за задача и какие навыки нужны"
}}

tech_work — нужна ли техническая работа Давида. Это true не только когда \
пишут код: настройка интеграций и сценариев (в том числе в no-code вроде n8n, \
Make, GetCourse), парсинг и сбор данных, обработка файлов и таблиц, \
распознавание речи, разбор документов, работа с моделями ИИ — всё это true.
false — когда нужен человек, а не система: ручной ввод данных, ведение \
соцсетей, тексты, дизайн, продажи, обучение.

employment — разовая или проектная работа, вакансия в штат, либо это вообще \
не заказ (реклама, резюме, самопиар исполнителя).

relevant=true только если tech_work=true и employment="проект"."""

DRAFT_PROMPT = f"""{PROFILE}

Ты пишешь от лица Давида короткий отклик на заказ. Отклик читает заказчик, \
который выбирает исполнителя из десятка откликов за минуту.

Правила:
- 4–6 предложений по-русски, живым языком, без канцелярита и самолюбования
- первое предложение — своими словами, ЧТО нужно сделать и как Давид это сделает
- одно предложение о надёжности: тесты, обработка ошибок, понятные сообщения \
о сбоях (8 лет в автоматизации тестирования)
- ровно один уточняющий вопрос ЗАКАЗЧИКУ про его задачу — то, что реально \
влияет на решение (не «есть ли опыт», не «расскажите подробнее»)
- максимум одна ссылка на портфолио, и только если она по теме
- опыт не выдумывать, сроки и цены не называть, гарантий не давать
- не перечислять библиотеки и технологии: заказчику важен результат, не стек
- приветствие не длиннее «Здравствуйте!», дальше сразу по делу
- сплошным текстом, без пустых строк, заголовков и списков
- в ответе только текст отклика, без кавычек и пояснений"""


@dataclass(frozen=True)
class Judgement:
    relevant: bool
    fit: int
    reason: str
    draft: str
    task: str = ""


def _chat(
    client: httpx.Client,
    base_url: str,
    model: str,
    system: str,
    user: str,
    timeout: float,
    as_json: bool,
    temperature: float,
) -> str:
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "stream": False,
        "options": {
            "temperature": temperature,
            "num_predict": 700,
            # Ollama defaults to 4096, and a long order plus this prompt
            # overflows that — the tail of the post would be dropped
            # silently, which is exactly where the requirements live.
            "num_ctx": 8192,
        },
    }
    if as_json:
        payload["format"] = "json"
    resp = client.post(f"{base_url}/api/chat", json=payload, timeout=timeout)
    resp.raise_for_status()
    return (resp.json().get("message") or {}).get("content", "")


def write_draft(
    client: httpx.Client,
    base_url: str,
    model: str,
    post_text: str,
    task: str = "",
    timeout: float = 120,
) -> str:
    """Second pass: a dedicated call for the reply, so the model writes prose
    instead of juggling prose and JSON. Empty string on any failure."""
    hint = f"\n\nСуть задачи: {task}" if task else ""
    try:
        text = _chat(
            client, base_url, model, DRAFT_PROMPT,
            f"Текст заказа:\n{post_text}{hint}\n\nНапиши отклик.",
            timeout, as_json=False, temperature=0.5,
        )
    except Exception:
        logger.warning("Draft generation failed, keeping template", exc_info=True)
        return ""
    return text.strip().strip('"')


def judge_lead(
    client: httpx.Client,
    base_url: str,
    model: str,
    post_text: str,
    timeout: float = 120,
    with_draft: bool = True,
) -> Judgement | None:
    """Returns a Judgement, or None if the model could not be consulted.

    Two passes: the verdict is decided first (structured, low temperature),
    and only an accepted lead costs a second call for the reply text.
    """
    try:
        raw = _chat(
            client, base_url, model, JUDGE_PROMPT,
            f"Текст поста:\n{post_text}", timeout, as_json=True, temperature=0.2,
        )
        data = json.loads(raw) if raw else None
    except Exception:
        logger.warning("LLM judge unavailable, keeping keyword verdict", exc_info=True)
        return None
    if not isinstance(data, dict):
        logger.warning("LLM judge returned unexpected payload: %r", data)
        return None

    fit = data.get("fit")
    task = str(data.get("task") or "").strip()[:200]
    # The model fills relevant itself, but the two grounding fields it wrote
    # first are the stricter check — trust them when they disagree.
    relevant = bool(data.get("relevant"))
    if data.get("tech_work") is False or data.get("employment") in {"штат", "не заказ"}:
        relevant = False

    draft = ""
    if relevant and with_draft:
        draft = write_draft(client, base_url, model, post_text, task, timeout)

    return Judgement(
        relevant=relevant,
        fit=fit if isinstance(fit, int) and 1 <= fit <= 3 else 2,
        reason=str(data.get("reason") or "").strip()[:120],
        draft=draft,
        task=task,
    )
