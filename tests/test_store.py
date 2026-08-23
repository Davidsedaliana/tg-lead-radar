import pytest

from tg_lead_radar.store import SeenStore


@pytest.fixture
def store(tmp_path):
    s = SeenStore(str(tmp_path / "seen.db"))
    yield s
    s.close()


def test_unseen_then_seen(store):
    assert not store.is_seen(-100, 1)
    store.mark_seen(-100, 1, ["bot"])
    assert store.is_seen(-100, 1)


def test_same_id_in_different_chats(store):
    store.mark_seen(-100, 1, ["bot"])
    assert not store.is_seen(-200, 1)


def test_mark_twice_is_idempotent(store):
    store.mark_seen(-100, 1, ["bot"])
    store.mark_seen(-100, 1, ["bot"])
    assert store.count() == 1


def test_survives_reopen(tmp_path):
    path = str(tmp_path / "seen.db")
    s1 = SeenStore(path)
    s1.mark_seen(-100, 5, ["fix"])
    s1.close()
    s2 = SeenStore(path)
    assert s2.is_seen(-100, 5)
    s2.close()
