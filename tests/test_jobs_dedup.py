from tg_lead_radar.jobs.config import load_jobs_config
from tg_lead_radar.jobs.dedup import (
    fingerprint,
    normalize,
    same_vacancy,
    shingles,
    similarity,
    title_key,
)

CFG = load_jobs_config().dedup

ORIGINAL = """QA Automation Engineer (Middle+)
Компания: FinTech Lab
Формат: удалёнка

Задачи:
— разработка автотестов на Python (pytest, Playwright)
— поддержка CI в GitLab
— ревью тест-кейсов и участие в планировании релизов

Требования:
— опыт автоматизации тестирования от 2 лет
— уверенный Python и понимание HTTP
— SQL на уровне запросов

Резюме: @hr_anna"""

CROSSPOST = (
    "🔥 #вакансия #qa #remote\n\n"
    + ORIGINAL
    + "\n\n➡️ Подписывайтесь: @it_jobs_feed\nБольше вакансий: t.me/it_jobs_feed"
)

OTHER_SAME_TITLE = """QA Automation Engineer (Middle+)
Компания: Ромашка Ритейл
Формат: офис

Чем заниматься:
— писать UI-тесты на Java и Selenide для интернет-магазина
— настраивать прогоны в Jenkins и разбирать падения
— общаться с разработчиками мобильного приложения

Ждём от вас:
— знание Java Core
— опыт с Allure и TestNG

Контакт: @romashka_hr"""


def fp(text, **fields):
    return fingerprint(text, fields, CFG)


class TestNormalize:
    def test_drops_wrappers(self):
        assert normalize("🔥 #вакансия QA-инженер! @hr t.me/x Ёлка") == "qa инженер елка"

    def test_title_key_skips_generic_lines(self):
        assert title_key("🔥 Новая вакансия!\nQA Engineer (Senior)", CFG.generic_titles) == (
            "qa engineer senior"
        )

    def test_short_text_shingles(self):
        assert shingles("qa engineer") == frozenset({"qa engineer"})
        assert similarity(frozenset(), frozenset({"a"})) == 0.0


class TestSameVacancy:
    def test_verbatim_copy(self):
        assert same_vacancy(fp(ORIGINAL), fp(ORIGINAL + "  "), CFG)

    def test_crosspost_with_other_header_and_footer(self):
        a, b = fp(ORIGINAL), fp(CROSSPOST)
        assert a.text_hash != b.text_hash
        assert same_vacancy(a, b, CFG)

    def test_different_vacancies_with_the_same_title_stay_apart(self):
        a, b = fp(ORIGINAL), fp(OTHER_SAME_TITLE)
        assert a.title_key == b.title_key
        assert not same_vacancy(a, b, CFG)

    def test_different_grades_stay_apart(self):
        senior = ORIGINAL.replace("Middle+", "Senior")
        a = fp(ORIGINAL, grade=["middle"])
        b = fp(senior, grade=["senior"])
        assert not same_vacancy(a, b, CFG)

    def test_overlapping_grades_do_not_block(self):
        a = fp(ORIGINAL, grade=["middle", "senior"])
        b = fp(CROSSPOST, grade=["middle"])
        assert same_vacancy(a, b, CFG)

    def test_different_salaries_stay_apart(self):
        a = fp(ORIGINAL, salary_from=250_000, currency="RUB")
        b = fp(CROSSPOST, salary_from=300_000, currency="RUB")
        assert not same_vacancy(a, b, CFG)

    def test_missing_salary_on_one_side_does_not_block(self):
        a = fp(ORIGINAL, salary_from=250_000, currency="RUB")
        assert same_vacancy(a, fp(CROSSPOST), CFG)
