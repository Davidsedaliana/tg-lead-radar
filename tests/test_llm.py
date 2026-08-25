import json

import httpx
import pytest

import tg_lead_radar.llm as llm
import tg_lead_radar.web as web
from tg_lead_radar.card import LeadCard
from tg_lead_radar.config import Settings
from tg_lead_radar.llm import Judgement, enrich_card, judge_lead
from tg_lead_radar.store import SeenStore
from tg_lead_radar.webpreview import Post


class FakeResponse:
    def __init__(self, content: str):
        self._content = content

    def raise_for_status(self):
        return None

    def json(self):
        return {"message": {"content": self._content}}


class FakeClient:
    """Serves one response per call: the judge pass, then the draft pass."""

    def __init__(self, *contents: str, error: Exception | None = None):
        self._contents = list(contents)
        self._error = error
        self.requests = []

    def post(self, url, **kwargs):
        self.requests.append((url, kwargs))
        if self._error:
            raise self._error
        content = self._contents.pop(0) if self._contents else ""
        return FakeResponse(content)


REASON = "Бот приёма заявок с записью в таблицу"
TASK = "бот приёма заявок"
DRAFT = "Здравствуйте! Сделаю бота..."


def payload(**overrides) -> str:
    data = {
        "task": TASK,
        "tech_work": True,
        "employment": "проект",
        "relevant": True,
        "fit": 3,
        "reason": REASON,
    }
    data.update(overrides)
    return json.dumps(data, ensure_ascii=False)


class TestJudgeLead:
    def test_parses_verdict_and_asks_for_draft(self):
        client = FakeClient(payload(), DRAFT)
        verdict = judge_lead(client, "http://x", "m", "Нужен бот")
        assert verdict == Judgement(
            relevant=True, fit=3, reason=REASON, draft=DRAFT, task=TASK
        )
        assert len(client.requests) == 2, "verdict pass, then draft pass"
        assert client.requests[0][1]["json"]["format"] == "json"
        assert "format" not in client.requests[1][1]["json"], "draft is plain text"

    def test_irrelevant_verdict_skips_draft_call(self):
        client = FakeClient(payload(relevant=False))
        verdict = judge_lead(client, "http://x", "m", "Ищем дизайнера")
        assert verdict is not None and not verdict.relevant
        assert len(client.requests) == 1

    def test_tech_work_false_overrides_relevant(self):
        client = FakeClient(payload(tech_work=False))
        assert not judge_lead(client, "http://x", "m", "t").relevant

    def test_staff_employment_overrides_relevant(self):
        client = FakeClient(payload(employment="штат"))
        assert not judge_lead(client, "http://x", "m", "t").relevant

    def test_not_an_order_overrides_relevant(self):
        client = FakeClient(payload(employment="не заказ"))
        assert not judge_lead(client, "http://x", "m", "t").relevant

    def test_out_of_range_fit_defaults_to_two(self):
        client = FakeClient(payload(fit=9), DRAFT)
        assert judge_lead(client, "http://x", "m", "t").fit == 2

    def test_draft_failure_leaves_draft_empty(self):
        client = FakeClient(payload(), "")
        verdict = judge_lead(client, "http://x", "m", "t")
        assert verdict.relevant and verdict.draft == ""

    def test_connection_error_returns_none(self):
        client = FakeClient(error=httpx.ConnectError("down"))
        assert judge_lead(client, "http://x", "m", "t") is None

    def test_broken_json_returns_none(self):
        assert judge_lead(FakeClient("not json"), "http://x", "m", "t") is None

    def test_non_object_payload_returns_none(self):
        assert judge_lead(FakeClient("[1, 2]"), "http://x", "m", "t") is None


def make_card() -> LeadCard:
    return LeadCard(
        channel_title="@chan",
        link="https://t.me/chan/1",
        text="Нужен бот",
        categories=["bot"],
        draft="ШАБЛОН",
        fit="⭐⭐",
    )


class TestEnrichCard:
    def settings(self) -> Settings:
        return Settings(bot_token="1:a", notify_user_id=1, llm_enabled=True, _env_file=None)

    def test_enriches_card(self, monkeypatch):
        monkeypatch.setattr(
            llm,
            "judge_lead",
            lambda *a, **k: Judgement(True, 3, "Профильная задача", "Персональный отклик"),
        )
        card = enrich_card(httpx.Client(), self.settings(), make_card())
        assert card.draft == "Персональный отклик"
        assert card.reason == "Профильная задача"
        assert card.fit == "⭐⭐⭐"

    def test_rejection_drops_card(self, monkeypatch):
        monkeypatch.setattr(
            llm, "judge_lead", lambda *a, **k: Judgement(False, 1, "не то", "")
        )
        assert enrich_card(httpx.Client(), self.settings(), make_card()) is None

    def test_model_down_keeps_card(self, monkeypatch):
        monkeypatch.setattr(llm, "judge_lead", lambda *a, **k: None)
        card = enrich_card(httpx.Client(), self.settings(), make_card())
        assert card.draft == "ШАБЛОН"
        assert card.fit == "⭐⭐"

    def test_empty_draft_falls_back_to_template(self, monkeypatch):
        monkeypatch.setattr(
            llm, "judge_lead", lambda *a, **k: Judgement(True, 2, "ок", "")
        )
        card = enrich_card(httpx.Client(), self.settings(), make_card())
        assert card.draft == "ШАБЛОН"


@pytest.fixture
def store(tmp_path):
    s = SeenStore(str(tmp_path / "seen.db"))
    yield s
    s.close()


WMS = "Разработка WMS для склада"


def test_order_feed_sends_post_keywords_would_miss(monkeypatch, store):
    settings = Settings(
        bot_token="1:a", notify_user_id=42, web_channels="feed",
        order_feeds="feed", llm_enabled=True, _env_file=None,
    )
    # No keyword in this text; only the order-feed bypass lets it reach the model.
    monkeypatch.setattr(web, "fetch_channel", lambda c, ch: [Post("feed", 1, WMS)])
    sent = []
    monkeypatch.setattr(web, "send_via_bot", lambda c, t, u, card: sent.append(card))
    monkeypatch.setattr(
        llm, "judge_lead", lambda *a, **k: Judgement(True, 3, "Бэкенд на Python", "Отклик")
    )

    assert web.poll_once(httpx.Client(), store, settings) == 1
    assert sent[0].reason == "Бэкенд на Python"


def test_ordinary_channel_still_gated_by_keywords(monkeypatch, store):
    settings = Settings(
        bot_token="1:a", notify_user_id=42, web_channels="chan",
        order_feeds="feed", llm_enabled=True, _env_file=None,
    )
    monkeypatch.setattr(web, "fetch_channel", lambda c, ch: [Post("chan", 1, WMS)])
    sent = []
    monkeypatch.setattr(web, "send_via_bot", lambda c, t, u, card: sent.append(card))
    monkeypatch.setattr(llm, "judge_lead", lambda *a, **k: Judgement(True, 3, "x", "y"))

    assert web.poll_once(httpx.Client(), store, settings) == 0


def test_poll_skips_llm_rejected_posts(monkeypatch, store):
    settings = Settings(
        bot_token="1:a", notify_user_id=42, web_channels="chan",
        llm_enabled=True, _env_file=None,
    )
    monkeypatch.setattr(
        web, "fetch_channel", lambda c, ch: [Post("chan", 1, "Нужен телеграм-бот")]
    )
    sent = []
    monkeypatch.setattr(web, "send_via_bot", lambda c, t, u, card: sent.append(card))
    monkeypatch.setattr(
        llm, "judge_lead", lambda *a, **k: Judgement(False, 1, "реклама", "")
    )

    assert web.poll_once(httpx.Client(), store, settings) == 0
    assert sent == []
    # still marked seen, so it is not re-judged next cycle
    assert store.is_seen("chan", 1)
