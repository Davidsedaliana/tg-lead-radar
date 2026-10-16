from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from telethon.tl import types

import tg_lead_radar.jobs.collector as collector
from tg_lead_radar.config import Settings
from tg_lead_radar.jobs.collector import JobsRadar, resolve_jobs_sources
from tg_lead_radar.jobs.config import load_jobs_config
from tg_lead_radar.jobs.store import JobsStore

CONFIG = load_jobs_config()

VACANCY = """QA Automation Engineer (Middle+)
Компания: FinTech Lab, удалёнка
ЗП: 250 000 – 320 000 ₽
Задачи: автотесты на Python (pytest, Playwright), поддержка CI
Требования: опыт автоматизации тестирования от 2 лет, SQL
Резюме: @hr_anna"""


def channel(cid, username, title=None):
    return SimpleNamespace(id=cid, username=username, title=title or username, broadcast=True)


QA = channel(100, "qa_jobs", "QA Jobs")
IT = channel(200, "it_remote", "IT Remote")
ORIGIN = channel(300, "origin", "Origin")


def msg(msg_id, text, chat=QA, days_ago=1.0, **kwargs):
    return types.Message(
        id=msg_id,
        peer_id=types.PeerChannel(chat.id),
        date=datetime.now(UTC) - timedelta(days=days_ago),
        message=text,
        post=True,
        views=kwargs.pop("views", 500),
        **kwargs,
    )


class FakeClient:
    def __init__(self, history=None, folders=(), entities=None):
        self.history = history or {}  # chat id -> messages
        self.folders = list(folders)
        self.entities = entities or {}
        self.sent = []
        self.min_ids = []

    async def __call__(self, request):
        return types.messages.DialogFilters(filters=self.folders)

    async def get_entity(self, target):
        key = getattr(target, "channel_id", None) or getattr(target, "user_id", None) or target
        if key not in self.entities:
            raise ValueError(f"no such entity: {target}")
        return self.entities[key]

    async def iter_messages(self, entity, limit=None, min_id=0):
        self.min_ids.append(min_id)
        messages = sorted(self.history.get(entity.id, []), key=lambda m: m.id, reverse=True)
        for m in [m for m in messages if m.id > min_id][:limit]:
            yield m

    async def get_messages(self, entity, ids=None):
        return next((m for m in self.history.get(entity.id, []) if m.id == ids), None)

    async def send_message(self, chat, text, **kwargs):
        self.sent.append((chat, text))


@pytest.fixture(autouse=True)
def no_pauses(monkeypatch):
    async def instant(_seconds):
        return None

    monkeypatch.setattr(collector.asyncio, "sleep", instant)


@pytest.fixture
def store(tmp_path):
    s = JobsStore(str(tmp_path / "jobs.db"))
    yield s
    s.close()


def settings(tmp_path, **overrides) -> Settings:
    base = dict(
        jobs_enabled=True, jobs_folder="", jobs_export_dir=str(tmp_path / "out"),
        jobs_backfill_days=14, _env_file=None,
    )
    base.update(overrides)
    return Settings(**base)


def radar(client, store, tmp_path, sources=(QA,), **overrides) -> JobsRadar:
    r = JobsRadar(client, store, settings(tmp_path, **overrides), CONFIG)
    r.entities = list(sources)
    r._refreshed_at = datetime.now(UTC)
    return r


class TestSources:
    async def test_folder_peers_and_explicit_channels(self, tmp_path):
        folder = types.DialogFilter(
            id=2,
            title=types.TextWithEntities(text="💼 Вакансии", entities=[]),
            pinned_peers=[types.InputPeerChannel(100, 0)],
            include_peers=[types.InputPeerUser(5, 0), types.InputPeerChannel(100, 0)],
            exclude_peers=[],
        )
        hr = types.User(id=5, first_name="HR")
        client = FakeClient(folders=[folder], entities={100: QA, 5: hr, "it_remote": IT})
        s = settings(tmp_path, jobs_folder="Вакансии", jobs_channels="it_remote")
        entities = await resolve_jobs_sources(client, s)
        assert [e.username for e in entities] == ["qa_jobs", "it_remote"]

    async def test_missing_folder_falls_back_to_channel_list(self, tmp_path):
        client = FakeClient(entities={"it_remote": IT})
        s = settings(tmp_path, jobs_folder="Вакансии", jobs_channels="it_remote,nope")
        assert [e.username for e in await resolve_jobs_sources(client, s)] == ["it_remote"]


class TestPolling:
    async def test_first_read_loads_only_the_backfill_window(self, store, tmp_path):
        client = FakeClient({100: [
            msg(1, VACANCY.replace("FinTech", "Old"), days_ago=20),
            msg(2, VACANCY, days_ago=1),
        ]})
        assert await radar(client, store, tmp_path).poll() == 1
        [rec] = store.records()
        assert rec["message_link"] == "https://t.me/qa_jobs/2"
        assert store.last_msg_id(100) == 2

    async def test_later_polls_read_only_newer_posts(self, store, tmp_path):
        client = FakeClient({100: [msg(1, VACANCY)]})
        r = radar(client, store, tmp_path)
        await r.poll()
        client.history[100].append(msg(2, VACANCY.replace("FinTech Lab", "Ромашка")
                                         .replace("250 000 – 320 000", "200 000")))
        assert await r.poll() == 1
        assert client.min_ids == [0, 1]
        assert store.count() == 2

    async def test_quiet_channel_remembers_its_newest_post(self, store, tmp_path):
        client = FakeClient({100: [msg(7, "Старый пост", days_ago=40)]})
        r = radar(client, store, tmp_path)
        await r.poll()
        await r.poll()
        assert store.last_msg_id(100) == 7
        assert client.min_ids == [0, 7]  # not read from the beginning again

    async def test_non_vacancies_are_skipped(self, store, tmp_path):
        client = FakeClient({100: [
            msg(1, "Python-разработчик, удалёнка"),
            msg(2, "#резюме QA Engineer, 3 года опыта"),
            msg(3, "Курс по автотестам, старт потока в ноябре"),
        ]})
        assert await radar(client, store, tmp_path).poll() == 0
        assert store.count() == 0

    async def test_record_fields(self, store, tmp_path):
        client = FakeClient({100: [msg(1, VACANCY, views=1520)]})
        await radar(client, store, tmp_path).poll()
        [rec] = store.records()
        assert rec["text"] == VACANCY
        assert (rec["salary_from"], rec["salary_to"], rec["currency"]) == (
            250_000, 320_000, "RUB",
        )
        assert rec["format"] == "remote"
        assert rec["grade"] == ["middle"]
        assert rec["contacts"] == ["@hr_anna"]
        assert rec["views"] == 1520
        assert {"qa", "автотест", "pytest"} <= set(rec["matched_stems"])

    async def test_digests_are_written(self, store, tmp_path):
        client = FakeClient({100: [msg(1, VACANCY)]})
        await radar(client, store, tmp_path).poll()
        assert list((tmp_path / "out").glob("*.md"))


class TestDuplicates:
    async def test_crosspost_is_one_vacancy_with_two_sources(self, store, tmp_path):
        crosspost = "🔥 #вакансия #qa\n\n" + VACANCY + "\n\nПодписывайтесь: @it_remote"
        client = FakeClient({
            100: [msg(1, VACANCY, days_ago=2)],
            200: [msg(9, crosspost, chat=IT, days_ago=1)],
        })
        assert await radar(client, store, tmp_path, sources=(QA, IT)).poll() == 1
        [rec] = store.records()
        assert [s["message_link"] for s in rec["sources"]] == [
            "https://t.me/qa_jobs/1", "https://t.me/it_remote/9",
        ]

    async def test_same_post_is_not_stored_twice(self, store, tmp_path):
        client = FakeClient({100: [msg(1, VACANCY)]})
        r = radar(client, store, tmp_path)
        post = collector.build_raw_post(client.history[100][0], QA)
        assert await r.process(post) is not None
        assert await r.process(post) is None


class TestOriginals:
    async def test_repost_by_link_fetches_the_original(self, store, tmp_path):
        client = FakeClient(
            {
                100: [msg(1, "Новая вакансия: https://t.me/origin/5")],
                300: [msg(5, VACANCY, chat=ORIGIN, days_ago=3, views=4000)],
            },
            entities={"origin": ORIGIN},
        )
        assert await radar(client, store, tmp_path).poll() == 1
        [rec] = store.records()
        assert rec["text"] == "Новая вакансия: https://t.me/origin/5"
        assert rec["original"]["text"] == VACANCY
        assert rec["original"]["message_link"] == "https://t.me/origin/5"
        assert rec["salary_from"] == 250_000  # fields come from the original
        assert rec["channel"] == "qa_jobs"  # the watched channel stays primary
        assert {s["kind"] for s in rec["sources"]} == {"post", "link"}

    async def test_unreachable_original_keeps_the_post_alone(self, store, tmp_path):
        client = FakeClient({100: [msg(1, "QA Engineer: https://t.me/private_x/5")]})
        assert await radar(client, store, tmp_path).poll() == 1
        assert store.records()[0]["original"] is None

    async def test_forward_records_its_origin(self, store, tmp_path):
        fwd = types.MessageFwdHeader(
            date=datetime.now(UTC) - timedelta(days=3),
            from_id=types.PeerChannel(300), channel_post=5,
        )
        client = FakeClient({100: [msg(1, VACANCY, fwd_from=fwd)]})
        await radar(client, store, tmp_path).poll()
        [rec] = store.records()
        assert rec["original"]["kind"] == "forward"
        assert [s["kind"] for s in rec["sources"]] == ["forward", "post"]


class TestAttachments:
    async def test_album_files_are_listed_on_the_captioned_post(self, store, tmp_path):
        def doc(name):
            return types.MessageMediaDocument(document=types.Document(
                id=1, access_hash=1, file_reference=b"", date=None, mime_type="application/pdf",
                size=10, dc_id=1, attributes=[types.DocumentAttributeFilename(name)],
            ))

        client = FakeClient({100: [
            msg(1, VACANCY, media=doc("JD.pdf"), grouped_id=77),
            msg(2, "", media=doc("Benefits.pdf"), grouped_id=77),
        ]})
        await radar(client, store, tmp_path).poll()
        [rec] = store.records()
        assert [a["file_name"] for a in rec["attachments"]] == ["JD.pdf", "Benefits.pdf"]


class TestCards:
    async def test_no_cards_by_default(self, store, tmp_path):
        client = FakeClient({100: [msg(1, VACANCY)]})
        r = radar(client, store, tmp_path)
        await r.poll()
        client.history[100].append(msg(2, VACANCY.replace("FinTech Lab", "Ромашка")
                                         .replace("250 000 – 320 000", "200 000")))
        await r.poll()
        assert client.sent == []

    async def test_cards_only_after_the_first_read(self, store, tmp_path):
        client = FakeClient({100: [msg(1, VACANCY)]})
        r = radar(client, store, tmp_path, jobs_notify=True)
        await r.poll()
        assert client.sent == []  # backfill does not flood Saved Messages
        client.history[100].append(msg(2, VACANCY.replace("FinTech Lab", "Ромашка")
                                         .replace("250 000 – 320 000", "200 000")))
        await r.poll()
        [(chat, text)] = client.sent
        assert chat == "me" and text.startswith("💼 QA Automation Engineer")


async def test_unreadable_channel_does_not_break_the_cycle(store, tmp_path):
    class Broken(FakeClient):
        async def iter_messages(self, entity, limit=None, min_id=0):
            if entity.id == 200:
                raise RuntimeError("private")
            async for m in super().iter_messages(entity, limit, min_id):
                yield m

    client = Broken({100: [msg(1, VACANCY)]})
    assert await radar(client, store, tmp_path, sources=(IT, QA)).poll() == 1
