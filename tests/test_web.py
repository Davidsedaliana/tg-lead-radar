import httpx
import pytest

import tg_lead_radar.web as web
from tg_lead_radar.config import Settings
from tg_lead_radar.store import SeenStore
from tg_lead_radar.webpreview import Post


@pytest.fixture
def store(tmp_path):
    s = SeenStore(str(tmp_path / "seen.db"))
    yield s
    s.close()


def make_settings() -> Settings:
    return Settings(
        bot_token="123:abc",
        notify_user_id=42,
        web_channels="chan",
        _env_file=None,
    )


def lead_post(msg_id: int, text: str = "Нужен бот для заявок") -> Post:
    return Post(channel="chan", msg_id=msg_id, text=text)


def run_poll(monkeypatch, store, posts, sent):
    monkeypatch.setattr(web, "fetch_channel", lambda client, ch: posts)
    monkeypatch.setattr(
        web, "send_via_bot", lambda client, token, uid, text: sent.append((uid, text))
    )
    return web.poll_once(httpx.Client(), store, make_settings())


def test_first_poll_trims_backlog(monkeypatch, store):
    sent = []
    posts = [lead_post(i) for i in range(1, 7)]  # 6 matching leads
    count = run_poll(monkeypatch, store, posts, sent)
    assert count == web.BACKLOG_NOTIFY_LIMIT
    # freshest ids survive the trim
    assert all("t.me/chan/" in text for _, text in sent)


def test_second_poll_only_new_posts(monkeypatch, store):
    sent = []
    run_poll(monkeypatch, store, [lead_post(1)], sent)
    sent.clear()
    count = run_poll(monkeypatch, store, [lead_post(1), lead_post(2)], sent)
    assert count == 1
    assert "t.me/chan/2" in sent[0][1]


def test_non_lead_posts_marked_but_not_sent(monkeypatch, store):
    sent = []
    count = run_poll(monkeypatch, store, [Post("chan", 5, "Всем привет, как дела?")], sent)
    assert count == 0
    assert store.is_seen("chan", 5)


def test_fetch_failure_does_not_crash(monkeypatch, store):
    def boom(client, ch):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(web, "fetch_channel", boom)
    assert web.poll_once(httpx.Client(), store, make_settings()) == 0


def test_card_goes_to_configured_user(monkeypatch, store):
    sent = []
    run_poll(monkeypatch, store, [lead_post(9)], sent)
    assert sent[0][0] == 42
