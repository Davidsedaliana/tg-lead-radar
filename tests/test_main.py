import pytest

import tg_lead_radar.main as main
from tg_lead_radar.config import Settings


class FakeJobs:
    def __init__(self, fail=False):
        self.polls = 0
        self.fail = fail

    async def poll(self):
        self.polls += 1
        if self.fail:
            raise RuntimeError("jobs broke")
        return 2


@pytest.fixture
def order_polls(monkeypatch):
    calls = []

    async def fake_poll_sources(client, entities, store, settings, http):
        calls.append(entities)
        return 0

    monkeypatch.setattr(main, "poll_sources", fake_poll_sources)
    return calls


SETTINGS = Settings(_env_file=None)


async def test_both_modes_in_one_cycle(order_polls):
    jobs = FakeJobs()
    await main.run_cycle(None, ["orders_chan"], jobs, None, SETTINGS, None)
    assert order_polls == [["orders_chan"]]
    assert jobs.polls == 1


async def test_jobs_only(order_polls):
    jobs = FakeJobs()
    await main.run_cycle(None, [], jobs, None, SETTINGS, None)
    assert order_polls == []
    assert jobs.polls == 1


async def test_orders_only(order_polls):
    await main.run_cycle(None, ["orders_chan"], None, None, SETTINGS, None)
    assert order_polls == [["orders_chan"]]


async def test_a_failing_mode_does_not_stop_the_other(order_polls):
    jobs = FakeJobs(fail=True)
    await main.run_cycle(None, ["orders_chan"], jobs, None, SETTINGS, None)
    await main.run_cycle(None, ["orders_chan"], jobs, None, SETTINGS, None)
    assert len(order_polls) == 2 and jobs.polls == 2


async def test_both_modes_off_is_refused(monkeypatch):
    off = Settings(api_id=1, api_hash="x", orders_enabled=False, jobs_enabled=False,
                   _env_file=None)
    monkeypatch.setattr(main, "load_settings", lambda: off)
    with pytest.raises(SystemExit, match="Оба режима выключены"):
        await main.run()
