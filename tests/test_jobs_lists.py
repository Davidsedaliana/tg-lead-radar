import pytest

from tg_lead_radar.jobs.lists import item_context, list_items, vacancy_url_key

# Shapes of real list posts (October 2026), shortened.
YOUNG_JUNE = """Подборка Junior/Стажёр удалённо

Разработка:
- Стажёр NLP в SimbirSoft (https://youngjunior.ru/data/jobs/61421?utm_source=telegram)
- QA Engineer - Automation & Manual в TG @jobforqa (https://youngjunior.ru/qa/jobs/61468?utm_source=telegram)

Дизайн/QA:
- Junior Cloud Security Validation Engineer в upwind (https://youngjunior.ru/qa/jobs/61397)

Вакансии (https://t.me/+L3DCwtFGhB8yM2Fi) │ Стажировки (https://t.me/+LgchVFqUUlBjYjcy)

🐣 Софи - первый ai ассистент для автооткликов (https://t.me/+r2QLDYuu_fE3NjY8)"""

TWO_ITEM_DIGEST = """Подборка джуновских вакансий

Backend:
- Junior Python Developer в ИП Маштак (https://youngjunior.ru/python/jobs/61998)

Аналитика:
- Manual QA в Flexbby (https://youngjunior.ru/qa/jobs/62002)"""

COMPANY_ROLES = """🔹 Senior Product Designer (https://buymie.jobs.personio.com/job/2797292)
🔹 Senior Data Analyst (https://buymie.jobs.personio.com/job/2794742)
🔹 Senior QA Engineer (https://buymie.jobs.personio.com/job/2794743)
🔹 Senior DevOps Engineer (https://buymie.jobs.personio.com/job/2795141)
Buymie is an online marketplace that connects Customers with personal shoppers.
On-site (Armenia)."""

INGAMEJOB = """#top_vacancies_inGameJob
A new week brings new jobs by InGame Job (https://ingamejob.com/en) and VV (https://vv.com/)👇

👉 QA / Testing

Head of QA (Product Quality) (https://ingamejob.com/job/head-of-qa-product-quality) (X-FLOW) Remote
Senior QA (https://ingamejob.com/job/senior-qa-3) (Redux Games) Remote
VR Data Operator (https://ingamejob.com/job/vr-data-operator) (Pixward Studios) Remote

More vacancies in Telegram — @ingamejob_qa (https://t.me/ingamejob_qa)"""

ONE_VACANCY = """QA Automation Engineer
Компания ищет инженера в команду платёжного шлюза, работа удалённая.
Задачи: писать автотесты на Python, поддерживать CI и разбирать падения.
Требования: опыт автоматизации от 3 лет, понимание HTTP и REST API.
Откликнуться (https://hh.ru/vacancy/1)
Сайт (https://company.com)
Мы в LinkedIn (https://linkedin.com/company/x)"""


class TestListItems:
    def test_digest_items_with_titles_and_links(self):
        items = list_items(YOUNG_JUNE)
        assert [i.title for i in items] == [
            "Стажёр NLP в SimbirSoft",
            "QA Engineer - Automation & Manual в TG @jobforqa",
            "Junior Cloud Security Validation Engineer в upwind",
        ]
        assert items[1].url.startswith("https://youngjunior.ru/qa/jobs/61468")
        assert [i.index for i in items] == [1, 2, 3]

    def test_two_items_under_a_digest_header(self):
        assert len(list_items(TWO_ITEM_DIGEST)) == 2

    def test_one_item_under_a_digest_header(self):
        text = "Подборка джуновских вакансий\n\nFrontend:\n- Junior QA в Keepgo (https://x.ru/qa/jobs/1)"
        assert [i.title for i in list_items(text)] == ["Junior QA в Keepgo"]

    def test_roles_of_one_company(self):
        assert len(list_items(COMPANY_ROLES)) == 4

    def test_items_with_text_after_the_link(self):
        items = list_items(INGAMEJOB)
        assert items[1].title == "Senior QA (Redux Games) Remote"

    def test_a_vacancy_with_several_links_is_not_a_list(self):
        assert list_items(ONE_VACANCY) is None

    def test_short_vacancy_with_links_to_different_sites_is_not_a_list(self):
        text = (
            "QA Engineer\nУдалёнка\nОткликнуться (https://hh.ru/vacancy/1)\n"
            "Сайт (https://company.com)\nLinkedIn (https://linkedin.com/company/x)"
        )
        assert list_items(text) is None

    def test_plain_text(self):
        assert list_items("QA Engineer\nУдалёнка") is None

    def test_context_keeps_shared_lines_only(self):
        context = item_context(YOUNG_JUNE, list_items(YOUNG_JUNE))
        assert context == "Подборка Junior/Стажёр удалённо"
        assert "On-site (Armenia)." in item_context(COMPANY_ROLES, list_items(COMPANY_ROLES))


class TestVacancyUrlKey:
    @pytest.mark.parametrize(
        ("url", "key"),
        [
            ("https://hh.ru/vacancy/123?from=tg", "hh.ru/vacancy/123"),
            ("https://www.ingamejob.com/job/senior-qa-3/", "ingamejob.com/job/senior-qa-3"),
            ("https://youngjunior.ru/qa/jobs/61468?utm_source=x", "youngjunior.ru/qa/jobs/61468"),
            ("https://geekjob.ru/vacancy/6aa1d929f4a230ca", "geekjob.ru/vacancy/6aa1d929f4a230ca"),
        ],
    )
    def test_links_to_one_vacancy(self, url, key):
        assert vacancy_url_key(url) == key

    @pytest.mark.parametrize(
        "url",
        [
            "https://company.com/careers",
            "https://company.com",
            "https://t.me/qa_jobs/123",
            "https://docs.google.com/forms/d/e/abc/viewform",
        ],
    )
    def test_shared_links(self, url):
        assert vacancy_url_key(url) is None
