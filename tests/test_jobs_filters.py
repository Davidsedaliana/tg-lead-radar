import pytest

from tg_lead_radar.jobs.config import load_jobs_config
from tg_lead_radar.jobs.filters import match_job
from tg_lead_radar.jobs.text import find_emails, find_urls, headline_zone, strip_links

CFG = load_jobs_config().filter

AQA_VACANCY = """#вакансия #qa #python #удаленка

QA Automation Engineer (Middle+)
Компания: FinTech Lab
Формат: удалёнка, РФ
ЗП: 250 000 – 320 000 ₽ на руки

Задачи:
— разработка автотестов на Python (pytest, Playwright)
— поддержка CI в GitLab

Мы предлагаем: обучение за счёт компании, менторство от senior-коллег, ДМС.
Присылайте резюме: @hr_anna"""

MANUAL_VACANCY = """Ищем тестировщика (manual) в офис
Москва, м. Тульская, 5/2
Опыт тестирования веб-приложений от 1 года
Зарплата до 150 000 ₽
Резюме на hr@shop.ru"""

ENGLISH_VACANCY = """We're hiring a Senior SDET!
Remote, EU timezone. Stack: Java, Selenide, Allure.
Apply: https://jobs.lever.co/company/123"""


class TestVacanciesPass:
    @pytest.mark.parametrize("text", [AQA_VACANCY, MANUAL_VACANCY, ENGLISH_VACANCY])
    def test_real_vacancies(self, text):
        assert match_job(text, CFG).matched

    def test_matched_stems_are_reported(self):
        r = match_job(AQA_VACANCY, CFG)
        assert {"qa", "автотест", "pytest", "playwright"} <= set(r.stems)

    def test_multiword_stem_with_endings(self):
        r = match_job("Инженер по автоматизации тестирования, Java", CFG)
        assert r.stems == ["автоматизац тестир"]

    def test_hashtag_qa_counts(self):
        assert match_job("#qa #вакансия\nИнженер по качеству", CFG).matched

    def test_perks_and_send_your_cv_do_not_reject(self):
        # "обучение", "менторство", "резюме" in the body are normal for a vacancy
        assert match_job(AQA_VACANCY, CFG).rejected_by is None


class TestNotQaVacancies:
    def test_other_role(self):
        assert not match_job("Python-разработчик (Django), удалёнка, 300к", CFG).matched

    def test_channel_footer_handle_does_not_count(self):
        text = "Backend-разработчик Go\nУдалёнка, 350к\n\nБольше вакансий: @qa_jobs_daily"
        assert not match_job(text, CFG).matched

    def test_link_slug_does_not_count(self):
        text = "Frontend-разработчик React\nhttps://career.example.com/qa-team/frontend"
        assert not match_job(text, CFG).matched

    @pytest.mark.parametrize("text", ["", "Всем привет!"])
    def test_irrelevant(self, text):
        assert not match_job(text, CFG).matched


class TestRejected:
    @pytest.mark.parametrize(
        "text",
        [
            "🔥 Курс «Автоматизатор тестирования на Python»\n"
            "Старт потока 1 ноября. Пишем автотесты с нуля, рассрочка 0%",
            "Хочешь в QA? Бесплатный вебинар по автотестам в четверг!",
            "Обучение тестировщиков с гарантией трудоустройства",
            "#курсы #qa\nPlaywright с нуля за 6 недель",
        ],
    )
    def test_courses(self, text):
        assert not match_job(text, CFG).matched

    @pytest.mark.parametrize(
        "text",
        [
            "#резюме\nQA Engineer, 4 года опыта, pytest, Selenium. Хочу удалёнку",
            "Ищу работу QA Automation, Python/pytest, 3 года опыта",
            "#whois QA-инженер, открыт к предложениям",
            "Резюме: Senior QA, Java + Selenide",
        ],
    )
    def test_candidates(self, text):
        assert not match_job(text, CFG).matched

    @pytest.mark.parametrize(
        "text",
        [
            "Менторство для QA: помогу пройти собеседование и выйти на оффер",
            "Ищу ментора по автотестам на Python",
        ],
    )
    def test_mentoring(self, text):
        assert not match_job(text, CFG).matched

    @pytest.mark.parametrize(
        "text",
        [
            "#фриланс Нужен QA-специалист на разовую задачу: прогнать регресс",
            "Нужен тестировщик мобильного приложения, бюджет: 15 000 ₽",
            "Ищем QA на разовый проект, оплата за проект",
        ],
    )
    def test_one_off_orders(self, text):
        assert not match_job(text, CFG).matched

    @pytest.mark.parametrize(
        "text",
        [
            "Реклама. ООО «Школа», erid: 2VtzqxK\nСтаньте QA-инженером за 4 месяца",
            "Розыгрыш курса по Selenium среди подписчиков!",
        ],
    )
    def test_ads(self, text):
        assert not match_job(text, CFG).matched

    def test_rejection_reason_is_reported(self):
        r = match_job("#резюме QA Engineer", CFG)
        assert r.stems and r.rejected_by == "anti: #резюме"


class TestTextHelpers:
    def test_find_urls_in_order_with_scheme(self):
        text = "Откликнуться: hh.ru/vacancy/1, анкета https://forms.gle/x. Канал t.me/qa_jobs"
        assert find_urls(text) == [
            "https://hh.ru/vacancy/1",
            "https://forms.gle/x",
            "https://t.me/qa_jobs",
        ]

    def test_find_urls_skips_emails_and_stack_words(self):
        assert find_urls("Пишите на hr@shop.ru, стек ASP.NET и Node.JS") == []

    def test_emails(self):
        assert find_emails("Резюме: HR@Shop.ru или jobs@x.io.") == ["hr@shop.ru", "jobs@x.io"]

    def test_strip_links_removes_handles_but_keeps_hashtags(self):
        assert "qa" not in strip_links("@qa_jobs https://t.me/qa").lower()
        assert "#qa" in strip_links("#qa @hr")

    def test_headline_zone_skips_leading_hashtags(self):
        text = "#вакансия #qa\nQA Engineer\n#remote\nОписание"
        assert headline_zone(text) == "#вакансия #qa\nQA Engineer\n#remote"
