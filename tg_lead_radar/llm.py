"""Optional local-LLM draft writer (Ollama).

When enabled, a local model rewrites the reply draft for the specific order.
Any failure falls back to the static template — the radar never depends on
the model being up. Nothing leaves the machine.
"""

import logging

import httpx

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """Ты пишешь отклик на фриланс-заказ от лица Давида.

Факты о Давиде — использовать ТОЛЬКО их, ничего не выдумывать:
- Python-разработчик: Telegram-боты (aiogram), автоматизация процессов, \
интеграции систем (Google Sheets, CRM, API)
- Отдельная специализация: локальный ИИ — распознавание речи (Whisper на GPU), \
обработка документов без облака, когда данные нельзя отдавать наружу
- 8 лет в автоматизации тестирования в крупном финтехе: тесты, CI и обработка \
ошибок в каждом проекте
- Портфолио: https://github.com/Davidsedaliana (бот с интеграцией: \
lead-intake-bot, локальный ИИ: voicetype-local)

Правила отклика:
- 4–6 предложений по-русски, без воды и канцелярита
- Первое предложение — про задачу заказчика своими словами и как Давид её решит
- Один уточняющий вопрос по делу
- Максимум одна ссылка на релевантный проект портфолио
- Срок и цену не называть — оставить плейсхолдер вида [срок], если просятся
- Приветствие — не длиннее «Здравствуйте!», никаких «готов приступить немедленно»
- Ответ — только текст отклика, без пояснений и кавычек"""


def generate_draft(
    client: httpx.Client,
    base_url: str,
    model: str,
    post_text: str,
    timeout: float = 90,
) -> str | None:
    """Returns a personalised draft, or None so the caller keeps the template."""
    try:
        resp = client.post(
            f"{base_url}/api/chat",
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": f"Текст заказа:\n{post_text}\n\nНапиши отклик."},
                ],
                "stream": False,
                "options": {"temperature": 0.4, "num_predict": 400},
            },
            timeout=timeout,
        )
        resp.raise_for_status()
        draft = (resp.json().get("message") or {}).get("content", "").strip()
        return draft or None
    except Exception:
        logger.warning("LLM draft failed, falling back to template", exc_info=True)
        return None
