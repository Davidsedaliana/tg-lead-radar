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

PROFILE = """Профиль исполнителя (Давид):
- Python-разработчик: Telegram-боты (aiogram), автоматизация бизнес-процессов, \
интеграции систем (Google Sheets, CRM, REST API), парсинг через официальные API
- Локальный ИИ: распознавание речи (Whisper на своей GPU), обработка документов \
без облака — когда данные нельзя отдавать во внешние сервисы
- 8 лет автоматизации тестирования в крупном финтехе: тесты, CI, обработка ошибок
- НЕ берёт: вёрстку и лендинги, дизайн, SMM, маркетинг, фронтенд-разработку \
(React/Vue), работу в штат на полный день
- Портфолио: github.com/Davidsedaliana (lead-intake-bot — бот заявок с интеграцией; \
voicetype-local — локальная диктовка на GPU)"""

JUDGE_PROMPT = f"""{PROFILE}

Тебе дают текст поста из Telegram-канала. Определи, это подходящий Давиду \
ЗАКАЗ (фриланс, проектная или разовая работа) — или нет.

Не подходит: вакансии в штат и на фуллтайм, реклама курсов и сервисов, \
самопиар других исполнителей и студий, резюме, посты не по профилю \
(дизайн, SMM, маркетинг, вёрстка, фронтенд, продажи).

Ответь СТРОГО одним JSON-объектом:
{{
  "relevant": true|false,
  "fit": 1|2|3,
  "reason": "одна строка (до 90 символов): что за задача и при чём тут навыки Давида",
  "draft": "текст отклика или пустая строка, если relevant=false"
}}

fit: 3 — прямо в профиль (боты, автоматизация, интеграции, локальный ИИ, \
починка кода); 2 — смежное, взяться можно; 1 — сомнительно.

Правила для draft (только если relevant=true):
- 4–6 предложений по-русски, без воды и канцелярита
- первое предложение — про задачу заказчика своими словами и как Давид её решит
- один уточняющий вопрос по делу
- максимум одна ссылка на релевантный проект портфолио
- опыт не выдумывать, срок и цену не называть
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
