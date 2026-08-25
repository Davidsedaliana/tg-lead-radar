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

Ответь СТРОГО одним JSON-объектом:
{{
  "relevant": true|false,
  "fit": 1|2|3,
  "reason": "одна строка до 90 символов: что за задача и какие навыки нужны",
  "draft": "текст отклика или пустая строка, если relevant=false"
}}

Правила для draft (только если relevant=true):
- 4–6 предложений по-русски, живым языком, без канцелярита
- первое предложение — своими словами, ЧТО нужно сделать и как Давид это сделает
- затем одно предложение о том, почему ему можно доверить: опыт с похожими \
задачами, тесты и обработка ошибок
- ровно один уточняющий вопрос ЗАКАЗЧИКУ про его задачу (не про опыт Давида)
- максимум одна ссылка на портфолио, если она уместна
- опыт не выдумывать, сроки и цены не называть
- приветствие не длиннее «Здравствуйте!»"""


@dataclass(frozen=True)
class Judgement:
    relevant: bool
    fit: int
    reason: str
    draft: str


def _ask(
    client: httpx.Client, base_url: str, model: str, prompt: str, user: str, timeout: float
) -> dict | None:
    resp = client.post(
        f"{base_url}/api/chat",
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": prompt},
                {"role": "user", "content": user},
            ],
            "stream": False,
            "format": "json",
            "options": {"temperature": 0.3, "num_predict": 500},
        },
        timeout=timeout,
    )
    resp.raise_for_status()
    content = (resp.json().get("message") or {}).get("content", "")
    return json.loads(content) if content else None


def judge_lead(
    client: httpx.Client,
    base_url: str,
    model: str,
    post_text: str,
    timeout: float = 120,
) -> Judgement | None:
    """Returns a Judgement, or None if the model could not be consulted."""
    try:
        data = _ask(
            client, base_url, model, JUDGE_PROMPT,
            f"Текст поста:\n{post_text}", timeout,
        )
    except Exception:
        logger.warning("LLM judge unavailable, keeping keyword verdict", exc_info=True)
        return None
    if not isinstance(data, dict):
        logger.warning("LLM judge returned unexpected payload: %r", data)
        return None
    fit = data.get("fit")
    return Judgement(
        relevant=bool(data.get("relevant")),
        fit=fit if isinstance(fit, int) and 1 <= fit <= 3 else 2,
        reason=str(data.get("reason") or "").strip()[:120],
        draft=str(data.get("draft") or "").strip(),
    )
