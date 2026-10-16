import dataclasses
from datetime import UTC, datetime, timedelta

import pytest
from telethon.errors import FloodWaitError
from telethon.tl import types
from telethon.tl.functions.channels import (
    GetChannelRecommendationsRequest,
    GetFullChannelRequest,
)
from telethon.tl.functions.contacts import SearchRequest

from tg_lead_radar.jobs.config import load_jobs_config
from tg_lead_radar.jobs.discover import (
    ALIVE,
    DEAD,
    DOUBTFUL,
    ChannelStats,
    DiscoveryResult,
    compute_stats,
    discover,
    label,
    report_rows,
    score,
    write_reports,
)

CONFIG = load_jobs_config()
CFG = CONFIG.discover
NOW = datetime(2026, 10, 16, 12, 0, tzinfo=UTC)
VACANCY = "QA Automation Engineer, pytest, удалёнка, 300к"


def chan(cid, username, broadcast=True, megagroup=False, left=True, subscribers=1000):
    return types.Channel(
        id=cid, title=username.upper(), photo=types.ChatPhotoEmpty(), date=None,
        username=username, broadcast=broadcast, megagroup=megagroup, left=left,
        participants_count=subscribers, access_hash=1,
    )


def post(msg_id, text, days_ago, views=100, forwarded=False):
    fwd = types.MessageFwdHeader(date=NOW) if forwarded else None
    return types.Message(
        id=msg_id, peer_id=types.PeerChannel(1), date=NOW - timedelta(days=days_ago),
        message=text, views=views, fwd_from=fwd,
    )


def stats(**overrides) -> ChannelStats:
    base = dict(
        channel_id=1, username="qa_new", title="QA New", subscribers=1000, posts_sampled=100,
        days_since_last=1.0, posts_7d=10, avg_views=150.0, views_per_sub=0.15,
        qa_share=0.4, ad_share=0.0, repost_share=0.0, junk_share=0.0,
    )
    base.update(overrides)
    return ChannelStats(**base)


class TestComputeStats:
    def test_metrics(self):
        messages = [
            post(1, VACANCY, days_ago=1, views=200),
            post(2, "Курс по QA со скидкой! erid: abc", days_ago=2, views=100),
            post(3, "Python-разработчик в офис", days_ago=10, views=300, forwarded=True),
            post(4, VACANCY + " Middle", days_ago=12, views=200),
        ]
        s = compute_stats(chan(1, "qa_new"), messages, 1000, NOW, CONFIG, ["поиск «QA jobs»"])
        assert s.posts_sampled == 4
        assert s.days_since_last == pytest.approx(1.0)
        assert s.posts_7d == 2
        assert s.avg_views == 200
        assert s.views_per_sub == pytest.approx(0.2)
        assert s.qa_share == 0.5  # the course is not a vacancy
        assert s.ad_share == 0.25
        assert s.repost_share == 0.25
        assert s.junk_share == 0.5
        assert s.link == "https://t.me/qa_new"

    def test_empty_channel(self):
        s = compute_stats(chan(1, "empty"), [], None, NOW, CONFIG)
        assert s.days_since_last is None and s.qa_share == 0.0 and s.views_per_sub is None

    def test_service_messages_are_not_posts(self):
        service = types.MessageService(
            id=9, peer_id=types.PeerChannel(1), date=NOW, action=types.MessageActionPinMessage()
        )
        s = compute_stats(chan(1, "x"), [service, post(1, VACANCY, 1)], 100, NOW, CONFIG)
        assert s.posts_sampled == 1


class TestLabel:
    def test_alive(self):
        assert label(stats(), CFG.thresholds) == ALIVE

    @pytest.mark.parametrize(
        "overrides",
        [
            {"days_since_last": 5.0},  # last post too old for "alive"
            {"posts_7d": 2},  # fewer than 3 a week
            {"qa_share": 0.05},  # mostly not QA
        ],
    )
    def test_doubtful(self, overrides):
        assert label(stats(**overrides), CFG.thresholds) == DOUBTFUL

    @pytest.mark.parametrize("days", [30.0, 90.0, None])
    def test_dead(self, days):
        assert label(stats(days_since_last=days), CFG.thresholds) == DEAD

    def test_thresholds_come_from_config(self):
        lenient = dataclasses.replace(CFG.thresholds, alive_min_posts_week=1)
        assert label(stats(posts_7d=1), lenient) == ALIVE


class TestScore:
    def test_ideal_channel_scores_100(self):
        s = stats(posts_7d=7, qa_share=0.3, views_per_sub=0.1)
        assert score(s, CFG) == 100.0

    def test_dead_channel_scores_zero(self):
        assert score(stats(days_since_last=None), CFG) == 0.0

    def test_staleness_lowers_the_score(self):
        assert score(stats(days_since_last=10.0), CFG) < score(stats(), CFG)

    def test_junk_is_penalised(self):
        assert score(stats(junk_share=0.6), CFG) == pytest.approx(score(stats(), CFG) * 0.7, 0.01)


class FakeClient:
    """Answers the three discover requests; get_messages per channel id."""

    def __init__(self, search=None, recommendations=None, history=None, flood=None):
        self.search = search or {}
        self.recommendations = recommendations or {}
        self.history = history or {}
        self.flood = list(flood or [])  # seconds to raise before the next answers
        self.requests = []

    async def __call__(self, request):
        self.requests.append(request)
        if self.flood:
            raise FloodWaitError(request=None, capture=self.flood.pop(0))
        if isinstance(request, SearchRequest):
            return types.contacts.Found(
                my_results=[], results=[], chats=self.search.get(request.q, []), users=[]
            )
        if isinstance(request, GetChannelRecommendationsRequest):
            return types.messages.Chats(chats=self.recommendations.get(request.channel.id, []))
        if isinstance(request, GetFullChannelRequest):
            full = types.ChannelFull.__new__(types.ChannelFull)
            full.participants_count = 5000
            return types.messages.ChatFull(full_chat=full, chats=[], users=[])
        raise AssertionError(f"unexpected request {request!r}")

    async def get_messages(self, entity, limit=None):
        return self.history.get(entity.id, [])


def config_with(**discover_overrides):
    return dataclasses.replace(
        CONFIG, discover=dataclasses.replace(CFG, queries=("QA jobs",), **discover_overrides)
    )


async def no_sleep(_seconds):
    return None


class TestDiscover:
    async def test_candidates_exclude_subscribed_groups_and_folder(self):
        source = chan(10, "qa_folder", left=False)
        fresh = chan(1, "qa_new")
        joined = chan(2, "qa_joined", left=False)
        in_dialogs = chan(3, "qa_dialog")
        group = chan(4, "qa_chat", broadcast=False, megagroup=True)
        client = FakeClient(
            search={"QA jobs": [fresh, joined, in_dialogs, group, source]},
            recommendations={10: [fresh]},
            history={1: [post(1, VACANCY, days_ago=1)]},
        )
        result = await discover(client, config_with(), [source], {3}, sleep=no_sleep, now=NOW)
        assert result.candidates_total == 1
        [s] = result.stats
        assert s.username == "qa_new"
        assert s.found_via == ("поиск «QA jobs»", "похож на @qa_folder")

    async def test_groups_when_enabled(self):
        group = chan(4, "qa_chat", broadcast=False, megagroup=True)
        client = FakeClient(search={"QA jobs": [group]})
        result = await discover(
            client, config_with(include_groups=True), [], set(), sleep=no_sleep, now=NOW
        )
        assert result.candidates_total == 1

    async def test_candidate_limit_prefers_repeated_finds(self):
        once, twice = chan(1, "qa_once", subscribers=9000), chan(2, "qa_twice", subscribers=10)
        source = chan(10, "qa_folder", left=False)
        client = FakeClient(search={"QA jobs": [once, twice]}, recommendations={10: [twice]})
        result = await discover(
            client, config_with(max_candidates=1), [source], set(), sleep=no_sleep, now=NOW
        )
        assert [s.username for s in result.stats] == ["qa_twice"]

    async def test_subscribers_from_full_channel_when_missing(self):
        bare = chan(1, "qa_new", subscribers=None)
        client = FakeClient(search={"QA jobs": [bare]})
        result = await discover(client, config_with(), [], set(), sleep=no_sleep, now=NOW)
        assert result.stats[0].subscribers == 5000

    async def test_short_flood_wait_is_waited_out(self):
        slept = []

        async def sleep(seconds):
            slept.append(seconds)

        client = FakeClient(search={"QA jobs": [chan(1, "qa_new")]}, flood=[30])
        result = await discover(client, config_with(), [], set(), sleep=sleep, now=NOW)
        assert result.stopped is None and len(result.stats) == 1
        assert 31 in slept

    async def test_long_flood_wait_stops_with_a_partial_report(self, tmp_path):
        client = FakeClient(search={"QA jobs": [chan(1, "qa_new")]}, flood=[3600])
        result = await discover(client, config_with(), [], set(), sleep=no_sleep, now=NOW)
        assert result.stopped and "3600" in result.stopped
        md, _ = write_reports(result, CFG, tmp_path, now=NOW)
        assert "⚠️" in md.read_text(encoding="utf-8")

    async def test_pause_between_requests(self):
        slept = []

        async def sleep(seconds):
            slept.append(seconds)

        client = FakeClient(search={"QA jobs": []})
        await discover(client, config_with(), [], set(), sleep=sleep, now=NOW)
        assert slept == [CFG.pause_seconds]

    async def test_only_read_requests_are_made(self):
        source = chan(10, "qa_folder", left=False)
        client = FakeClient(
            search={"QA jobs": [chan(1, "qa_new", subscribers=None)]},
            recommendations={10: []},
        )
        await discover(client, config_with(), [source], set(), sleep=no_sleep, now=NOW)
        allowed = (SearchRequest, GetChannelRecommendationsRequest, GetFullChannelRequest)
        assert all(isinstance(r, allowed) for r in client.requests)


class TestReport:
    def test_sorted_by_score_with_links(self, tmp_path):
        result_stats = [
            stats(username="weak", title="Weak", qa_share=0.0, days_since_last=20.0),
            stats(username="strong", title="Strong | QA"),
        ]
        rows = report_rows(result_stats, CFG)
        assert [r["link"] for r in rows] == ["https://t.me/strong", "https://t.me/weak"]
        md, table = write_reports(DiscoveryResult(result_stats, 5), CFG, tmp_path, now=NOW)
        text = md.read_text(encoding="utf-8")
        assert "кандидатов найдено: 5, проверено: 2" in text
        assert "| 1 | [Strong / QA](https://t.me/strong) | живой |" in text
        assert "| 40% |" in text
        csv_text = table.read_text(encoding="utf-8-sig")
        assert csv_text.splitlines()[0].startswith("Скор,Статус,Ссылка,Канал")
        assert "https://t.me/strong" in csv_text.splitlines()[1]
