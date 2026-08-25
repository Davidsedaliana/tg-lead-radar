import pytest

import tg_lead_radar.llm as llm
from tg_lead_radar.config import Settings
from tg_lead_radar.llm import Judgement
from tg_lead_radar.radar import build_card, handle_event
from tg_lead_radar.store import SeenStore
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


class FakeChat:
    def __init__(self, title="Чат", username=None, megagroup=False):
        self.title = title
        self.username = username
        self.megagroup = megagroup


class FakeEvent:
    def __init__(self, text, chat, msg_id=1, chat_id=-1001):
        self.raw_text = text
        self.id = msg_id
        self.chat_id = chat_id
        self.message = type("M", (), {"date": None})()
        self._chat = chat

    async def get_chat(self):
        return self._chat


class FakeTelethon:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat, text, **kwargs):
        self.sent.append((chat, text))


@pytest.fixture
def store(tmp_path):
    s = SeenStore(str(tmp_path / "seen.db"))
    yield s
    s.close()


def settings(**overrides) -> Settings:
    base = dict(api_id=1, api_hash="x", notify_chat="me", llm_enabled=True, _env_file=None)
    base.update(overrides)
    return Settings(**base)


class TestHandleEvent:
    async def test_llm_verdict_reaches_saved_messages(self, store, monkeypatch):
        monkeypatch.setattr(
            llm, "judge_lead", lambda *a, **k: Judgement(True, 3, "Профильно", "Отклик")
        )
        client = FakeTelethon()
        event = FakeEvent("Нужен телеграм-бот", FakeChat(username="chan"))

        assert await handle_event(event, store, settings(), client, http=object())
        chat, text = client.sent[0]
        assert chat == "me"
        assert "Профильно" in text
        assert "Отклик" in text

    async def test_llm_rejection_sends_nothing(self, store, monkeypatch):
        monkeypatch.setattr(
            llm, "judge_lead", lambda *a, **k: Judgement(False, 1, "реклама", "")
        )
        client = FakeTelethon()
        event = FakeEvent("Нужен телеграм-бот", FakeChat(username="chan"))

        assert not await handle_event(event, store, settings(), client, http=object())
        assert client.sent == []

    async def test_chat_message_without_keywords_reaches_the_model(self, store, monkeypatch):
        """In a group chat people phrase orders in plain speech."""
        monkeypatch.setattr(
            llm, "judge_lead", lambda *a, **k: Judgement(True, 2, "Разбор данных", "Отклик")
        )
        client = FakeTelethon()
        event = FakeEvent("Ребята, кто возьмётся посчитать остатки на складе?",
                          FakeChat(title="Бизнес чат", megagroup=True))

        assert await handle_event(event, store, settings(), client, http=object())

    async def test_same_message_twice_notifies_once(self, store, monkeypatch):
        monkeypatch.setattr(
            llm, "judge_lead", lambda *a, **k: Judgement(True, 3, "ок", "Отклик")
        )
        client = FakeTelethon()
        chat = FakeChat(username="chan")
        assert await handle_event(FakeEvent("Нужен бот", chat), store, settings(), client,
                                  http=object())
        assert not await handle_event(FakeEvent("Нужен бот", chat, msg_id=2), store,
                                      settings(), client, http=object())
        assert len(client.sent) == 1
