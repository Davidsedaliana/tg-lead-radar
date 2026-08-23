from tg_lead_radar.radar import build_card
from tg_lead_radar.templates import DRAFTS


def test_lead_produces_card_with_draft():
    built = build_card(
        text="Нужен телеграм-бот приёма заявок, бюджет 40к",
        chat_id=-1001,
        msg_id=10,
        chat_title="Диджитал Тендер",
        chat_username="digitaltender",
    )
    assert built is not None
    card, categories = built
    assert "bot" in categories
    assert "https://t.me/digitaltender/10" in card
    assert DRAFTS["bot"] in card


def test_non_lead_returns_none():
    assert build_card("Всем привет!", -1001, 11, "Чат", "chat") is None


def test_selfpromo_returns_none():
    assert (
        build_card("Предлагаю услуги: сделаю бота недорого", -1001, 12, "Чат", "chat") is None
    )
