"""Regression check for the judge prompt.

Unit tests cover the plumbing; prompt quality can only be checked against a
real model. Run this after touching JUDGE_PROMPT or changing the model:

    python eval/judge_cases.py [--model qwen2.5:7b]

Needs a running Ollama. Cases are real posts (shortened) from the watched
channels, labelled by hand.
"""

import argparse

import httpx

from tg_lead_radar.llm import judge_lead

CASES: list[tuple[str, bool]] = [
    (
        "📁 Скрипты, боты и mini apps | 💰 5 000 ₽ | ⏰ 10 дн.\n"
        "Создание бота по сбору информации\n"
        "Необходимо создать бота, который собирает заявки от пользователей "
        "и складывает в таблицу",
        True,
    ),
    ("📌 Доделать mini apps\nНужно проверить бота, исправить ошибки и ускорить работу", True),
    (
        "📁 Десктоп программирование | 💰 200 000 ₽\nРазработка WMS\n"
        "Требуется разработать веб-WMS систему для склада, Python/бэкенд",
        True,
    ),
    ("Нужна выгрузка заказов из amoCRM в Google Таблицу раз в час, бюджет 30к", True),
    ("Нужно распознать 200 часов записей звонков в текст, данные нельзя в облако", True),
    (
        "👔 Технический специалист / интегратор автоворонок\n"
        "Задача: настроить интеграции Instagram, Telegram и GetCourse; реализовать "
        "Telegram-бота приёма заявок. Проектная работа, оплата за задачу",
        True,
    ),
    ("📁 Создание сайта | 💰 50 000 ₽\nСверстать корпоративный сайт по макету Figma", False),
    ("📁 Мобильные приложения | 💰 100 000 ₽\nРазработка приложения под iOS и Android", False),
    ("Ищем Frontend-разработчика (React/TypeScript) в штат, оклад 200к, ДМС", False),
    ("Нужен дизайнер для логотипа и фирменного стиля", False),
    ("Курс по нейросетям со скидкой 50%, промокод AI50, регистрируйся", False),
]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="qwen2.5:7b")
    parser.add_argument("--url", default="http://127.0.0.1:11434")
    parser.add_argument("--drafts", action="store_true", help="печатать черновики откликов")
    args = parser.parse_args()

    passed = 0
    with httpx.Client(timeout=180) as client:
        for text, expected in CASES:
            verdict = judge_lead(client, args.url, args.model, text)
            if verdict is None:
                print("Ollama недоступна — запустите её и повторите")
                return 2
            good = verdict.relevant == expected
            passed += good
            head = text.split("\n")[0][:55]
            print(
                f"{'OK  ' if good else 'FAIL'} ожидалось={expected} "
                f"получено={verdict.relevant} fit={verdict.fit} | {head}"
            )
            if not good:
                print(f"      причина модели: {verdict.reason}")
            elif args.drafts and verdict.draft:
                print(f"      черновик: {verdict.draft}")

    print(f"\nИтог: {passed}/{len(CASES)}")
    return 0 if passed == len(CASES) else 1


if __name__ == "__main__":
    raise SystemExit(main())
