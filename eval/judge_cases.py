"""Regression check for the judge prompt.

Unit tests cover the plumbing; prompt quality can only be checked against a
real model. Run this after touching JUDGE_PROMPT or changing the model:

    python eval/judge_cases.py [--model qwen2.5:14b] [--drafts]

Needs a running Ollama. Cases are real posts (shortened) from the watched
channels plus hand-written near-misses, labelled by hand.
"""

import argparse
import time

import httpx

from tg_lead_radar.llm import judge_lead

# (text, expected_relevant)
CASES: list[tuple[str, bool]] = [
    # --- clearly ours ---
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
    ("Нужен парсер Wildberries: цены и остатки в Excel, оплата 15к", True),
    ("Нужно исправить ошибку в Python-скрипте, падает при запуске на больших файлах", True),
    ("Доработка бота на aiogram: добавить приём оплаты и выгрузку в CRM", True),
    ("Требуется автоматизировать сбор отчётности из 1С в сводную таблицу", True),
    ("Нужно оцифровать 500 сканов договоров в текст с поиском по ним", True),
    ("Требуется чат-бот с ИИ для ответов клиентам по базе знаний компании", True),
    ("Настроить сценарий в n8n: заявка с сайта -> Telegram -> Google Sheets", True),
    (
        "👔 Технический специалист / интегратор автоворонок\n"
        "Задача: настроить интеграции Instagram, Telegram и GetCourse; реализовать "
        "Telegram-бота приёма заявок. Проектная работа, оплата за задачу",
        True,
    ),
    # --- not ours ---
    ("📁 Создание сайта | 💰 50 000 ₽\nСверстать корпоративный сайт по макету Figma", False),
    ("📁 Мобильные приложения | 💰 100 000 ₽\nРазработка приложения под iOS и Android", False),
    ("Ищем Frontend-разработчика (React/TypeScript) в штат, оклад 200к, ДМС", False),
    ("Нужен дизайнер для логотипа и фирменного стиля", False),
    ("Курс по нейросетям со скидкой 50%, промокод AI50, регистрируйся", False),
    ("📌 Работа с CRM\nНужен человек заносить заявки в амоCRM вручную, 2 часа в день", False),
    ("Требуется наполнить интернет-магазин товарами вручную, 300 карточек", False),
    ("Помочь настроить рекламу в Яндекс.Директ и посчитать конверсии", False),
    ("Ищу ментора по Python для занятий два раза в неделю, оплата почасовая", False),
    ("Ищу партнёра в стартап за долю, нужен технический сооснователь", False),
    ("Монтаж роликов из стрима, разовый заказ, студия ищет монтажёра", False),
]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="qwen2.5:14b")
    parser.add_argument("--url", default="http://127.0.0.1:11434")
    parser.add_argument("--drafts", action="store_true", help="печатать черновики откликов")
    args = parser.parse_args()

    passed = 0
    false_pos: list[str] = []
    false_neg: list[str] = []
    started = time.monotonic()
    with httpx.Client(timeout=300) as client:
        for text, expected in CASES:
            verdict = judge_lead(client, args.url, args.model, text)
            if verdict is None:
                print("Ollama недоступна — запустите её и повторите")
                return 2
            good = verdict.relevant == expected
            passed += good
            head = text.split("\n")[0][:55]
            print(
                f"{'OK  ' if good else 'FAIL'} ожидалось={int(expected)} "
                f"получено={int(verdict.relevant)} fit={verdict.fit} | {head}"
            )
            if not good:
                (false_pos if verdict.relevant else false_neg).append(head)
                print(f"      причина модели: {verdict.reason}")
            elif args.drafts and verdict.draft:
                print(f"      черновик: {verdict.draft}")

    elapsed = time.monotonic() - started
    print(f"\nМодель {args.model}: {passed}/{len(CASES)}, {elapsed:.0f}с "
          f"({elapsed / len(CASES):.1f}с на кейс)")
    if false_neg:
        print(f"Пропущено заказов ({len(false_neg)}): " + "; ".join(false_neg))
    if false_pos:
        print(f"Пропущено мусора ({len(false_pos)}): " + "; ".join(false_pos))
    return 0 if passed == len(CASES) else 1


if __name__ == "__main__":
    raise SystemExit(main())
