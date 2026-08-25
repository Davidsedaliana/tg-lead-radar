import httpx
import pytest

import tg_lead_radar.web as web
from tg_lead_radar.config import Settings
from tg_lead_radar.llm import generate_draft
from tg_lead_radar.store import SeenStore
from tg_lead_radar.webpreview import Post


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("boom", request=None, response=None)

    def json(self):
        return self._payload


class FakeClient:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.requests = []

    def post(self, url, **kwargs):
        self.requests.append((url, kwargs))
        if self.error:
            raise self.error
        return self.response


class TestGenerateDraft:
    def test_returns_content(self):
        client = FakeClient(FakeResponse({"message": {"content": " Отклик готов. "}}))
        draft = generate_draft(client, "http://x", "qwen2.5:7b", "Нужен бот")
        assert draft == "Отклик готов."
        url, kwargs = client.requests[0]
        assert url == "http://x/api/chat"
        assert "Нужен бот" in kwargs["json"]["messages"][1]["content"]

    def test_connection_error_returns_none(self):
        client = FakeClient(error=httpx.ConnectError("down"))
        assert generate_draft(client, "http://x", "m", "text") is None

    def test_empty_content_returns_none(self):
        client = FakeClient(FakeResponse({"message": {"content": ""}}))
        assert generate_draft(client, "http://x", "m", "text") is None


@pytest.fixture
def store(tmp_path):
    s = SeenStore(str(tmp_path / "seen.db"))
    yield s
    s.close()


def test_poll_uses_llm_draft_with_template_fallback(monkeypatch, store):
    settings = Settings(
        bot_token="123:abc", notify_user_id=42, web_channels="chan",
        llm_enabled=True, _env_file=None,
    )
    posts = [Post("chan", 1, "Нужен телеграм-бот для заявок")]
    sent = []
    monkeypatch.setattr(web, "fetch_channel", lambda client, ch: posts)
    monkeypatch.setattr(
        web, "send_via_bot", lambda client, token, uid, card: sent.append(card)
    )
    monkeypatch.setattr(web, "generate_draft", lambda *a, **k: "Персональный отклик")
    assert web.poll_once(httpx.Client(), store, settings) == 1
    assert sent[0].draft == "Персональный отклик"

    # LLM fails -> template stays
    sent.clear()
    posts2 = [Post("chan", 2, "Нужен телеграм-бот для магазина")]
    monkeypatch.setattr(web, "fetch_channel", lambda client, ch: posts2)
    monkeypatch.setattr(web, "generate_draft", lambda *a, **k: None)
    assert web.poll_once(httpx.Client(), store, settings) == 1
    assert "Здравствуйте!" in sent[0].draft
