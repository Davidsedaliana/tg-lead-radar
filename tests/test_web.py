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


def lead_post(msg_id: int, text: str | None = None) -> Post:
    # Distinct text per post by default: identical texts are deduplicated,
    # which is what test_repeated_ad_text_notified_once covers separately.
    return Post(channel="chan", msg_id=msg_id, text=text or f"Нужен бот для заявок №{msg_id}")


def run_poll(monkeypatch, store, posts, sent):
    monkeypatch.setattr(web, "fetch_channel", lambda client, ch: posts)
    monkeypatch.setattr(
        web, "send_via_bot", lambda client, token, uid, card: sent.append((uid, card))
    )
    return web.poll_once(httpx.Client(), store, make_settings())


def test_first_poll_trims_backlog(monkeypatch, store):
    sent = []
    posts = [lead_post(i) for i in range(1, 7)]  # 6 matching leads
    count = run_poll(monkeypatch, store, posts, sent)
    assert count == web.BACKLOG_NOTIFY_LIMIT
    # freshest ids survive the trim
    assert [card.link for _, card in sent] == [
        "https://t.me/chan/4", "https://t.me/chan/5", "https://t.me/chan/6"
    ]


def test_second_poll_only_new_posts(monkeypatch, store):
    sent = []
    run_poll(monkeypatch, store, [lead_post(1)], sent)
    sent.clear()
    count = run_poll(monkeypatch, store, [lead_post(1), lead_post(2)], sent)
    assert count == 1
    assert sent[0][1].link == "https://t.me/chan/2"


def test_non_lead_posts_marked_but_not_sent(monkeypatch, store):
    sent = []
    count = run_poll(monkeypatch, store, [Post("chan", 5, "Всем привет, как дела?")], sent)
    assert count == 0
    assert store.is_seen("chan", 5)


def test_repeated_ad_text_notified_once(monkeypatch, store):
    """A channel that reposts the same ad must not produce a card each time."""
    sent = []
    ad = "Нужен бот для заявок — реклама повторяется"
    run_poll(monkeypatch, store, [lead_post(1, ad)], sent)
    assert len(sent) == 1
    sent.clear()
    run_poll(monkeypatch, store, [lead_post(2, ad), lead_post(3, ad)], sent)
    assert sent == []


def test_fetch_failure_does_not_crash(monkeypatch, store):
    def boom(client, ch):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(web, "fetch_channel", boom)
    assert web.poll_once(httpx.Client(), store, make_settings()) == 0


def test_card_goes_to_configured_user(monkeypatch, store):
    sent = []
    run_poll(monkeypatch, store, [lead_post(9)], sent)
    assert sent[0][0] == 42
