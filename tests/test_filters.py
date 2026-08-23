import pytest

from tg_lead_radar.filters import match


class TestMatch:
    def test_bot_order(self):
        r = match("Нужен телеграм-бот для приёма заявок, бюджет 30к")
        assert r.matched
        assert "bot" in r.categories

    def test_bot_stem_does_not_fire_inside_rabota(self):
        r = match("Предлагаем удалённую работу в офисе мечты")
        assert "bot" not in r.categories

    def test_fix_order(self):
        r = match("Срочно починить скрипт, падает с ошибкой при запуске")
        assert r.matched
        assert "fix" in r.categories

    def test_automation_order(self):
        r = match("Автоматизация выгрузки из CRM в Google Sheets")
        assert r.matched
        assert "automation" in r.categories

    def test_ai_order(self):
        r = match("Нужна транскрибация звонков, whisper или аналог")
        assert r.matched
        assert "ai" in r.categories

    def test_python_order(self):
        r = match("Ищем разработчика на Python, проектная занятость")
        assert r.matched
        assert "python" in r.categories

    def test_multiword_stem(self):
        r = match("перенос данных в google   sheets")
        assert r.matched

    @pytest.mark.parametrize(
        "text",
        [
            "Ищу работу, рассмотрю проекты. Резюме в личке",
            "Предлагаю услуги по разработке ботов, портфолио в профиле",
            "#резюме Python-разработчик, 5 лет опыта",
            "Возьму заказ на парсинг, недорого",
        ],
    )
    def test_anti_kills_freelancer_selfpromo(self, text):
        assert not match(text).matched

    @pytest.mark.parametrize("text", ["", "Продам гараж", "Как дела, коллеги?"])
    def test_irrelevant(self, text):
        assert not match(text).matched

    def test_keywords_reported(self):
        r = match("Нужен бот и парсер на Python")
        assert "бот" in r.keywords
        assert any(k.startswith("парсер") for k in r.keywords)
