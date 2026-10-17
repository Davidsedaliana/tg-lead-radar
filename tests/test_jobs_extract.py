import dataclasses

import pytest

from tg_lead_radar.jobs.config import load_jobs_config
from tg_lead_radar.jobs.extract import (
    Salary,
    extract_apply_urls,
    extract_city,
    extract_company,
    extract_contacts,
    extract_fields,
    extract_format,
    extract_grade,
    extract_salary,
    extract_same_as,
    extract_stack,
    extract_title,
    grade_by_experience,
    top_grade,
)

CFG = load_jobs_config().extract


def salary(text: str) -> tuple:
    s = extract_salary(text, CFG)
    return s.salary_from, s.salary_to, s.currency


class TestSalary:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("от 200к", (200_000, None, "RUB")),
            ("200 000 – 300 000 ₽", (200_000, 300_000, "RUB")),
            ("$3000", (3000, 3000, "USD")),
            ("до 250 на руки", (None, 250_000, "RUB")),
            ("от 200к или 2500usd", (200_000, None, "RUB")),
            ("2500usd", (2500, 2500, "USD")),
            ("ЗП: 150-200 тыс. руб. gross", (150_000, 200_000, "RUB")),
            ("Вилка: 3 500 – 4 500 $", (3500, 4500, "USD")),
            ("💰 от 180 000 до 250 000 ₽ net", (180_000, 250_000, "RUB")),
            ("Зарплата: 250 000 руб.", (250_000, 250_000, "RUB")),
            ("€4k–5k", (4000, 5000, "EUR")),
            ("USD 3000-4000", (3000, 4000, "USD")),
            ("Оклад 120 т.р.", (120_000, 120_000, "RUB")),
            ("ЗП 200к+", (200_000, None, "RUB")),
            ("Вилка 250-350", (250_000, 350_000, "RUB")),
            ("Доход: 1,5 млн ₽ в год", (1_500_000, 1_500_000, "RUB")),
            ("до 4000 EUR gross", (None, 4000, "EUR")),
            ("Salary: from $5000 to $7000", (5000, 7000, "USD")),
            ("Доход: от 150 000 до 200 000 рублей", (150_000, 200_000, "RUB")),
        ],
    )
    def test_formats(self, text, expected):
        assert salary(text) == expected

    def test_salary_on_the_line_after_its_label(self):
        assert salary("Зарплата:\n200 000 – 250 000") == (200_000, 250_000, "RUB")

    def test_hourly_rate(self):
        s = extract_salary("Ставка 2000 ₽/час", CFG)
        assert (s.salary_from, s.currency, s.salary_period) == (2000, "RUB", "hour")

    def test_period_month_when_stated(self):
        assert extract_salary("$4000 per month", CFG).salary_period == "month"

    def test_no_period_when_not_stated(self):
        assert extract_salary("ЗП 200к", CFG).salary_period is None

    def test_free_standing_currency_is_used(self):
        assert salary("Вилка (USD): 3000-4000") == (3000, 4000, "USD")

    def test_foreign_currency_elsewhere_makes_currency_unknown(self):
        # amounts without a currency in a post that pays in USDT
        assert salary("ЗП 3000-4000\nВыплаты в USDT") == (3000, 4000, "USDT")
        assert salary("ЗП 200 000\nБонусы в EUR и USD") == (200_000, 200_000, None)

    def test_implausible_rouble_salary_keeps_amount_without_currency(self):
        assert salary("Зарплата: 3000-4000") == (3000, 4000, None)

    @pytest.mark.parametrize(
        "text",
        [
            "Опыт от 3 лет",
            "ЗП по договорённости",
            "Компенсация фитнеса 15 000 ₽",
            "Команда 200+ человек, работаем с 2015 года",
            "Испытательный срок 3 месяца",
            "Релиз 16.10, версия 2.5",
            "Реферальный бонус 50 000 ₽",
            "Звоните +7 999 123-45-67",
        ],
    )
    def test_not_a_salary(self, text):
        assert extract_salary(text, CFG) == Salary()

    def test_salary_line_wins_over_perk_line(self):
        text = "Компенсация обучения до 100 000 ₽\nЗП: от 250к"
        assert salary(text) == (250_000, None, "RUB")


class TestFormat:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("Формат: удалёнка", "remote"),
            ("#удаленка #qa", "remote"),
            ("Remote, EU timezone", "remote"),
            ("Офис в Москве, м. Тульская", "office"),
            ("Гибрид: 2 дня в офисе", "hybrid"),
            ("Hybrid (Berlin)", "hybrid"),
            ("Офис или удалёнка — на выбор", None),
            ("Python, pytest, Allure", None),
            # the freest format on offer wins
            ("Удалёнка или гибрид", "remote"),
            ("Формат: офис, гибрид или удалёнка", "remote"),
            ("Офис / гибрид", "hybrid"),
            # remote mentioned, not offered
            ("Гибрид, удалёнка не рассматривается", "hybrid"),
            ("Офис, без удалёнки", "office"),
        ],
    )
    def test_format(self, text, expected):
        assert extract_format(text, CFG) == expected


class TestSalaryGrossAndPeriod:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("ЗП: 250 000 ₽ на руки", False),
            ("ЗП: 200-250k net", False),
            ("Вилка 300-350 тыс gross", True),
            ("Оклад 200 000 ₽ до вычета НДФЛ", True),
            ("ЗП 200к", None),
        ],
    )
    def test_gross(self, text, expected):
        assert extract_salary(text, CFG).salary_gross is expected

    def test_dotnet_is_not_net(self):
        assert extract_salary("Стек .NET, ЗП 300к", CFG).salary_gross is None

    @pytest.mark.parametrize(
        ("text", "period"),
        [
            ("Ставка 8000 ₽ за смену", "shift"),
            ("Оплата 5000 ₽ в день", "day"),
            ("$1200 per week", "week"),
        ],
    )
    def test_more_periods(self, text, period):
        assert extract_salary(text, CFG).salary_period == period


class TestCity:
    def test_one_city(self):
        assert extract_city("Офис в Санкт-Петербурге, м. Чернышевская", CFG) == "Санкт-Петербург"

    def test_alias(self):
        assert extract_city("Локация: СПб", CFG) == "Санкт-Петербург"

    def test_several_cities_is_a_doubt(self):
        assert extract_city("Офисы в Москве и Казани", CFG) is None

    def test_time_zone_line_is_not_a_location(self):
        assert extract_city("Удалёнка, работа по времени Москвы (UTC+3)", CFG) is None
        assert extract_city("График с 10 до 19 по мск", CFG) is None


class TestGrade:
    def test_from_title(self):
        assert extract_grade("QA Automation Engineer (Middle+/Senior)\nОписание", CFG) == [
            "middle", "senior",
        ]

    def test_russian_forms(self):
        assert extract_grade("Ищем джуна в QA", CFG) == ["junior"]

    def test_lead(self):
        assert extract_grade("QA Lead / Тимлид тестирования", CFG) == ["lead"]

    def test_body_mentions_are_ignored(self):
        text = "QA Engineer\nКомпания X\nУдалёнка\nЗадачи:\n— менторить junior-коллег"
        assert extract_grade(text, CFG) is None

    def test_labelled_line_in_body(self):
        text = "QA Engineer\nКомпания X\nУдалёнка\nО нас...\nГрейд: Senior"
        assert extract_grade(text, CFG) == ["senior"]

    def test_hashtag_in_body(self):
        text = "QA Engineer\nКомпания X\nУдалёнка\nТекст\n#senior_qa #remote"
        assert extract_grade(text, CFG) == ["senior"]

    def test_leading_is_not_lead(self):
        assert extract_grade("QA в leading fintech company", CFG) is None


class TestGradeForTheExport:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("Опыт работы от 3 лет", "senior"),
            ("Опыт коммерческой автоматизации тестирования не менее 2 лет", "middle"),
            ("Требуемый опыт: 1–3 года", "middle"),
            ("3+ years of experience in QA", "senior"),
            ("Experience: 5+ years", "senior"),
            ("Можно без опыта, научим", "junior"),
            ("Компания на рынке 10 лет", None),
        ],
    )
    def test_by_experience(self, text, expected):
        assert grade_by_experience(text) == expected

    def test_top_grade_is_the_most_senior(self):
        assert top_grade(["middle", "senior"], CFG) == "senior"
        assert top_grade(["lead", "junior"], CFG) == "lead"
        assert top_grade(None, CFG) is None


class TestCompanyAndTitle:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("QA Engineer\nКомпания: FinTech Lab", "FinTech Lab"),
            ("🏢 Компания — Ромашка (https://romashka.ru).", "Ромашка"),
            ("Company: Acme Inc.", "Acme Inc"),
            ("Мы — продуктовая компания в финтехе", None),
        ],
    )
    def test_company_only_from_a_labelled_line(self, text, expected):
        assert extract_company(text) == expected

    def test_title_skips_hashtags_generic_lines_and_decoration(self):
        generic = load_jobs_config().dedup.generic_titles
        text = "#вакансия #qa\n🔥 Новая вакансия!\n🔥 QA Automation Engineer (Middle+)\nТекст"
        assert extract_title(text, generic) == "QA Automation Engineer (Middle+)"

    def test_title_of_empty_text(self):
        assert extract_title("#qa") == ""


class TestSameAs:
    def test_hh_links_become_hh_ids(self):
        urls = [
            "https://spb.hh.ru/vacancy/123456?from=share",
            "https://hh.ru/vacancy/123456",
            "https://hh.ru/employer/42",
            "https://career.habr.com/vacancies/1",
        ]
        assert extract_same_as(urls) == ["hh:123456"]


class TestStack:
    def test_intersection_with_dictionary(self):
        text = "Автотесты на Python (pytest, Playwright), CI в GitLab, Docker, PostgreSQL"
        assert extract_stack(text, CFG) == [
            "python", "pytest", "playwright", "docker", "gitlab ci", "postgresql",
        ]

    def test_word_boundaries(self):
        assert extract_stack("JavaScript и TypeScript", CFG) == ["javascript", "typescript"]
        assert extract_stack("Тесты на C# и .NET", CFG) == ["c#"]

    def test_nothing_found(self):
        assert extract_stack("Ручное тестирование", CFG) is None


class TestContacts:
    def test_handles_emails_and_links(self):
        text = (
            "Резюме: @hr_anna или hr@company.ru\n"
            "Вопросы — t.me/recruiter_bob\n"
            "Анна (tg://user?id=42)"
        )
        assert extract_contacts(text, [], CFG, set()) == [
            "@hr_anna", "hr@company.ru", "@recruiter_bob", "tg://user?id=42",
        ]

    def test_channel_lines_and_own_channel_skipped(self):
        text = "Пишите @hr_anna\nПодписывайтесь на @qa_jobs\nИсточник: @it_vacancies\n@qa_feed"
        assert extract_contacts(text, [], CFG, {"qa_feed"}) == ["@hr_anna"]

    def test_post_links_are_not_contacts(self):
        assert extract_contacts("Оригинал: https://t.me/qa_jobs/123", [], CFG, set()) is None

    def test_email_is_not_a_handle(self):
        assert extract_contacts("jobs@company.io", [], CFG, set()) == ["jobs@company.io"]

    def test_buttons(self):
        buttons = [
            ("Написать HR", "https://t.me/hr_anna"),
            ("Подписаться на канал", "https://t.me/qa_jobs"),
        ]
        assert extract_contacts("", buttons, CFG, set()) == ["@hr_anna"]


class TestApplyUrls:
    def test_boards_forms_and_career_pages(self):
        urls = [
            "https://spb.hh.ru/vacancy/123",
            "https://docs.google.com/forms/d/e/abc/viewform",
            "https://docs.google.com/document/d/xyz",
            "https://careers.company.com/qa",
            "https://company.com/jobs/qa-engineer",
            "https://t.me/qa_jobs",
            "https://company.com/about",
        ]
        assert extract_apply_urls(urls, CFG) == [
            "https://spb.hh.ru/vacancy/123",
            "https://docs.google.com/forms/d/e/abc/viewform",
            "https://careers.company.com/qa",
            "https://company.com/jobs/qa-engineer",
        ]

    def test_none(self):
        assert extract_apply_urls(["https://company.com"], CFG) is None


def test_extract_fields_shape():
    fields = extract_fields(
        "QA Engineer (Senior)\nУдалёнка\nЗП от 300к\nPython, pytest\n@hr_anna",
        ["https://hh.ru/vacancy/1"],
        [],
        CFG,
        set(),
    )
    assert fields == {
        "company": None,
        "salary_from": 300_000,
        "salary_to": None,
        "currency": "RUB",
        "salary_period": None,
        "salary_gross": None,
        "format": "remote",
        "city": None,
        "grade": ["senior"],
        "experience_grade": None,
        "stack": ["python", "pytest"],
        "contacts": ["@hr_anna"],
        "apply_urls": ["https://hh.ru/vacancy/1"],
        "same_as": ["hh:1"],
    }


def test_default_currency_can_be_turned_off():
    cfg = dataclasses.replace(CFG, default_currency=None)
    s = extract_salary("от 200к", cfg)
    assert (s.salary_from, s.currency) == (200_000, None)
