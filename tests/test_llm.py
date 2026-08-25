import json

import httpx
import pytest

import tg_lead_radar.web as web
from tg_lead_radar.card import LeadCard
from tg_lead_radar.config import Settings
from tg_lead_radar.llm import Judgement, judge_lead
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
    def __init__(self, content: str | None = None, error: Exception | None = None):
        self._content = content
        self._error = error
        self.requests = []

    def post(self, url, **kwargs):
        self.requests.append((url, kwargs))
        if self._error:
            raise self._error
        return FakeResponse(self._content or "")


def payload(**overrides) -> str:
    data = {
        "relevant": True,
        "fit": 3,
        "reason": "Бот приёма заявок с записью в таблицу — профильная задача",
        "draft": "Здравствуйте! Сделаю бота...",
    }
    data.update(overrides)
    return json.dumps(data, ensure_ascii=False)


class TestJudgeLead:
    def test_parses_verdict(self):
        client = FakeClient(payload())
        v = judge_lead(client, "http://x", "m", "Нужен бот")
        assert v == Judgement(
            relevant=True,
            fit=3,
            reason="Бот приёма заявок с записью в таблицу — профильная задача",
            draft="Здравствуйте! Сделаю бота...",
        )
        url, kwargs = client.requests[0]
        assert url == "http://x/api/chat"
        assert kwargs["json"]["format"] == "json"

    def test_irrelevant_verdict(self):
        client = FakeClient(payload(relevant=False, draft=""))
        v = judge_lead(client, "http://x", "m", "Ищем дизайнера")
        assert v is not None and not v.relevant

    def test_out_of_range_fit_defaults_to_two(self):
        assert judge_lead(FakeClient(payload(fit=9)), "http://x", "m", "t").fit == 2

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


class TestApplyLlm:
    def settings(self) -> Settings:
        return Settings(bot_token="1:a", notify_user_id=1, llm_enabled=True, _env_file=None)

    def test_enriches_card(self, monkeypatch):
        monkeypatch.setattr(
            web, "judge_lead",
            lambda *a, **k: Judgement(True, 3, "Профильная задача", "Персональный отклик"),
        )
        card = web.apply_llm(httpx.Client(), self.settings(), make_card())
        assert card.draft == "Персональный отклик"
        assert card.reason == "Профильная задача"
        assert card.fit == "⭐⭐⭐"

    def test_rejection_drops_card(self, monkeypatch):
        monkeypatch.setattr(web, "judge_lead", lambda *a, **k: Judgement(False, 1, "не то", ""))
        assert web.apply_llm(httpx.Client(), self.settings(), make_card()) is None

    def test_model_down_keeps_card(self, monkeypatch):
        monkeypatch.setattr(web, "judge_lead", lambda *a, **k: None)
        card = web.apply_llm(httpx.Client(), self.settings(), make_card())
        assert card.draft == "ШАБЛОН"
        assert card.fit == "⭐⭐"

    def test_empty_draft_falls_back_to_template(self, monkeypatch):
        monkeypatch.setattr(web, "judge_lead", lambda *a, **k: Judgement(True, 2, "ок", ""))
        assert web.apply_llm(httpx.Client(), self.settings(), make_card()).draft == "ШАБЛОН"


@pytest.fixture
def store(tmp_path):
    s = SeenStore(str(tmp_path / "seen.db"))
    yield s
    s.close()


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
    monkeypatch.setattr(web, "judge_lead", lambda *a, **k: Judgement(False, 1, "реклама", ""))

    assert web.poll_once(httpx.Client(), store, settings) == 0
    assert sent == []
    # still marked seen, so it is not re-judged next cycle
    assert store.is_seen("chan", 1)
