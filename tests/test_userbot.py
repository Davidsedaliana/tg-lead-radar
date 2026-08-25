from datetime import UTC, datetime, timedelta

import pytest

import tg_lead_radar.llm as llm
import tg_lead_radar.userbot as userbot
from tg_lead_radar.config import Settings
from tg_lead_radar.llm import Judgement
from tg_lead_radar.store import SeenStore


class FakeMessage:
    def __init__(self, msg_id, text, hours_ago=1):
        self.id = msg_id
        self.message = text
        self.date = datetime.now(UTC) - timedelta(hours=hours_ago)


class FakeEntity:
    def __init__(self, username="chan", title="Канал", megagroup=False, entity_id=-100):
        self.username = username
        self.title = title
        self.megagroup = megagroup
        self.id = entity_id


class FakeClient:
    def __init__(self, messages):
        self._messages = messages
        self.sent = []

    async def get_messages(self, entity, limit=25):
        return list(reversed(self._messages))  # Telethon returns newest first

    async def send_message(self, chat, text, **kwargs):
        self.sent.append((chat, text))


@pytest.fixture
def store(tmp_path):
    s = SeenStore(str(tmp_path / "seen.db"))
    yield s
    s.close()


def settings(**overrides) -> Settings:
    base = dict(api_id=1, api_hash="x", notify_chat="me", llm_enabled=False, _env_file=None)
    base.update(overrides)
    return Settings(**base)


class TestCollectNewCards:
    async def test_reads_a_source_it_is_not_subscribed_to(self, store):
        client = FakeClient([FakeMessage(1, "Нужен телеграм-бот для заявок")])
        cards = await userbot.collect_new_cards(client, FakeEntity(), store, settings())
        assert len(cards) == 1
        assert cards[0].link == "https://t.me/chan/1"

    async def test_second_poll_returns_only_new(self, store):
        entity = FakeEntity()
        first = [FakeMessage(1, "Нужен бот для заявок")]
        await userbot.collect_new_cards(FakeClient(first), entity, store, settings())
        second = first + [FakeMessage(2, "Нужен парсер цен на Python")]
        cards = await userbot.collect_new_cards(FakeClient(second), entity, store, settings())
        assert [c.link for c in cards] == ["https://t.me/chan/2"]

    async def test_first_poll_drops_stale_messages(self, store):
        client = FakeClient([
            FakeMessage(1, "Нужен бот для старого заказа", hours_ago=30 * 24),
            FakeMessage(2, "Нужен бот для свежего заказа", hours_ago=2),
        ])
        cards = await userbot.collect_new_cards(client, FakeEntity(), store, settings())
        assert [c.link for c in cards] == ["https://t.me/chan/2"]

    async def test_group_chat_bypasses_keyword_gate(self, store):
        """People phrase orders in plain speech there."""
        client = FakeClient([FakeMessage(1, "Ребята, кто посчитает остатки на складе?")])
        entity = FakeEntity(username=None, title="Бизнес чат", megagroup=True)
        cards = await userbot.collect_new_cards(
            client, entity, store, settings(llm_enabled=True)
        )
        assert len(cards) == 1

    async def test_channel_without_keywords_is_gated(self, store):
        client = FakeClient([FakeMessage(1, "Ребята, кто посчитает остатки на складе?")])
        cards = await userbot.collect_new_cards(
            FakeClient(client._messages), FakeEntity(), store, settings(llm_enabled=True)
        )
        assert cards == []

    async def test_ads_rejected_even_in_a_group(self, store):
        client = FakeClient([FakeMessage(1, "Курс по ботам со скидкой, промокод BOT50")])
        entity = FakeEntity(username=None, megagroup=True)
        cards = await userbot.collect_new_cards(
            client, entity, store, settings(llm_enabled=True)
        )
        assert cards == []


class TestPollSources:
    async def test_sends_cards_to_saved_messages(self, store, monkeypatch):
        monkeypatch.setattr(
            llm, "judge_lead", lambda *a, **k: Judgement(True, 3, "Профильно", "Отклик")
        )
        client = FakeClient([FakeMessage(1, "Нужен телеграм-бот")])
        sent = await userbot.poll_sources(
            client, [FakeEntity()], store, settings(llm_enabled=True), http=object()
        )
        assert sent == 1
        chat, text = client.sent[0]
        assert chat == "me"
        assert "Профильно" in text and "Отклик" in text

    async def test_llm_rejection_sends_nothing(self, store, monkeypatch):
        monkeypatch.setattr(
            llm, "judge_lead", lambda *a, **k: Judgement(False, 1, "реклама", "")
        )
        client = FakeClient([FakeMessage(1, "Нужен телеграм-бот")])
        sent = await userbot.poll_sources(
            client, [FakeEntity()], store, settings(llm_enabled=True), http=object()
        )
        assert sent == 0
        assert client.sent == []

    async def test_unreadable_source_does_not_break_the_cycle(self, store):
        class Broken(FakeClient):
            async def get_messages(self, entity, limit=25):
                if getattr(entity, "username", "") == "broken":
                    raise RuntimeError("private")
                return await super().get_messages(entity, limit)

        client = Broken([FakeMessage(1, "Нужен телеграм-бот")])
        sources = [FakeEntity(username="broken"), FakeEntity(username="chan")]
        sent = await userbot.poll_sources(client, sources, store, settings(), http=None)
        assert sent == 1
