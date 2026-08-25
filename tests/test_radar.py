from tg_lead_radar.radar import build_card
from tg_lead_radar.templates import DRAFTS


def test_lead_produces_card_with_draft():
    card = build_card(
        text="Нужен телеграм-бот приёма заявок, бюджет 40к",
        chat_id=-1001,
        msg_id=10,
        chat_title="Диджитал Тендер",
        chat_username="digitaltender",
    )
    assert card is not None
    assert "bot" in card.categories
    assert card.link == "https://t.me/digitaltender/10"
    assert card.draft == DRAFTS["bot"]


def test_non_lead_returns_none():
    assert build_card("Всем привет!", -1001, 11, "Чат", "chat") is None


class TestForce:
    """Order-feed channels: keywords do not gate, the LLM stage decides."""

    def test_force_builds_card_for_unmatched_text(self):
        card = build_card("Разработка WMS для склада", -1, 1, "@feed", "feed", force=True)
        assert card is not None
        assert card.categories == []

    def test_force_still_rejects_ads_and_selfpromo(self):
        assert build_card(
            "Курс по нейросетям, промокод AI50", -1, 2, "@feed", "feed", force=True
        ) is None
        assert build_card(
            "Предлагаю услуги: сделаю бота", -1, 3, "@feed", "feed", force=True
        ) is None

    def test_force_keeps_categories_when_keywords_match(self):
        card = build_card("Нужен телеграм-бот", -1, 4, "@feed", "feed", force=True)
        assert card is not None and "bot" in card.categories


def test_selfpromo_returns_none():
    assert (
        build_card("Предлагаю услуги: сделаю бота недорого", -1001, 12, "Чат", "chat") is None
    )
