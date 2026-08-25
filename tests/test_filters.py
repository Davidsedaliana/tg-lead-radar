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

    @pytest.mark.parametrize(
        "text",
        [
            "Курс по Python со скидкой 50%! erid: 2VtzqxK",
            "Розыгрыш подписки! Подпишись и участвуй",
            "Бесплатный урок: автоматизация в Excel. Регистрируйся по ссылке",
            "Интенсив по нейросетям, промокод BOT10",
        ],
    )
    def test_ads_are_rejected(self, text):
        assert not match(text).matched

    def test_offtopic_with_weak_keyword_is_rejected(self):
        # marketing post that merely mentions an API
        assert not match("Ищем таргетолога со знанием API рекламного кабинета").matched

    def test_offtopic_with_strong_keyword_passes(self):
        # a real dev order that also mentions design
        r = match("Нужен телеграм-бот, дизайнер для логотипа уже есть")
        assert r.matched

    def test_weak_only_without_veto_passes(self):
        assert match("Ищем разработчика на Python, проектная работа").matched

    def test_smm_post_mentioning_telegram_channel_rejected(self):
        assert not match("Ищут SMM-специалиста: ведение телеграм-канала, контент").matched

    def test_contact_line_telegram_does_not_fire(self):
        r = match("Вакансия: React-разработчик. Телеграм: @hr_contact")
        assert not r.matched

    def test_telegram_bot_order_still_passes(self):
        assert match("Нужен телеграм-бот для магазина").matched

    def test_digest_relevant_category_passes(self):
        text = (
            "Подборка заказов в категории Разработка (Боты и парсинг данных):\n"
            "1. Сверстать лендинг — 5000р\n2. Дизайн логотипа — 3000р"
        )
        assert match(text).matched

    def test_digest_irrelevant_category_rejected(self):
        text = (
            "Подборка заказов в категории Разработка (Фронтенд):\n"
            "1. Доработать скрипт корзины — 5000р\n2. Бот для сайта — 3000р"
        )
        assert not match(text).matched

    def test_frontend_vacancy_with_api_mention_rejected(self):
        text = (
            "#вакансия #react #frontend Ищут Frontend-разработчика (React).\n"
            "Задачи: редизайн, интеграция с API (Swagger), адаптация под SSR"
        )
        assert not match(text).matched

    def test_bot_order_mentioning_frontend_passes(self):
        assert match("Нужен телеграм-бот, фронтенд уже готов на React").matched

    def test_staff_vacancy_rejected(self):
        text = "Нужен разработчик ботов в штат, оклад 150к, ДМС, офис"
        assert not match(text).matched

    def test_project_work_with_staff_words_passes(self):
        text = "Разработчик ботов, проектная работа, оплата за задачу (не оклад)"
        assert match(text).matched

    @pytest.mark.parametrize(
        "text",
        [
            "👔 SEO-специалист (Middle)\nЗадачи: автоматизация отчётов, работа с ИИ",
            "👔 Графический дизайнер\nБудет плюсом: нейросети, автоматизация макетов",
            "#ищу #смм #контент\nПисать сценарии рилс в связке с ИИ",
            "Ассистент-администратор\nВести таблицы, автоматизировать отчёты",
            "#whois #дизайнер #вебдизайнер\nДоработка макетов, интеграция с API",
        ],
    )
    def test_offprofile_role_in_title_vetoes_whole_post(self, text):
        assert not match(text).matched

    def test_core_signal_in_title_survives_veto_word(self):
        assert match("Нужен бот для дизайнера\nАвтопостинг макетов в канал").matched

    @pytest.mark.parametrize(
        "text",
        [
            "Ищу себе позицию Project Manager, опыт 5 лет",
            "Ищете Fullstack-разработчика? Мы создаем решения для бизнеса",
            "Наша студия разрабатывает ботов под ключ",
            "Привет, меня зовут Кямиль. Пишу ботов и парсеры на Python",
        ],
    )
    def test_agency_and_jobseeker_selfpromo_rejected(self, text):
        assert not match(text).matched

    @pytest.mark.parametrize(
        "text",
        [
            "👔 Менеджер digital-проектов\nВести клиентов, автоматизировать отчёты",
            "👔 Специалист поддержки\nОтвечать в чатах, работа с CRM",
            "Ищем сотрудника для редактирования контента через Word",
        ],
    )
    def test_non_dev_roles_in_title_rejected(self, text):
        assert not match(text).matched

    def test_categories_ordered_by_dominance(self):
        text = "Автоматизация: скрипт выгрузки из CRM в Excel, отчёты. Python"
        r = match(text)
        assert r.categories[0] == "automation"
        assert r.cat_hits["automation"] >= 4

    def test_keywords_reported(self):
        r = match("Нужен бот и парсер на Python")
        assert "бот" in r.keywords
        assert any(k.startswith("парсер") for k in r.keywords)
