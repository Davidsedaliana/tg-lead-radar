from twitch_clipper.detector import SpikeDetector


def feed(det: SpikeDetector, start: float, seconds: int, per_second: float) -> float:
    """Feed evenly spaced messages, return the timestamp of the last one."""
    n = int(seconds * per_second)
    t = start
    for i in range(n):
        t = start + i / per_second
        det.add(t)
    return t


def test_quiet_chat_gives_no_spike():
    det = SpikeDetector(window=10, mult=3.0, min_messages=15)
    now = feed(det, 0, 300, 1.0)
    assert det.check(now) is None


def test_burst_is_detected_with_ratio():
    det = SpikeDetector(window=10, mult=3.0, min_messages=15)
    feed(det, 0, 200, 1.0)  # usual pace: 10 messages per window
    now = feed(det, 200, 10, 4.0)  # 40 messages in 10 seconds
    spike = det.check(now)
    assert spike is not None
    assert 3.5 < spike.ratio < 4.5
    assert spike.count >= 39


def test_cooldown_blocks_second_spike():
    det = SpikeDetector(window=10, mult=3.0, min_messages=15, cooldown=90)
    feed(det, 0, 200, 1.0)
    now = feed(det, 200, 10, 4.0)
    assert det.check(now) is not None
    now = feed(det, 210, 10, 4.0)
    assert det.check(now) is None
    feed(det, 220, 120, 1.0)
    now = feed(det, 340, 10, 5.0)
    assert det.check(now) is not None


def test_small_chat_needs_minimum_messages():
    det = SpikeDetector(window=10, mult=3.0, min_messages=15)
    feed(det, 0, 200, 0.1)  # one message per 10 seconds
    now = feed(det, 200, 10, 1.0)  # 10 messages: 10x the usual, but still a tiny chat
    assert det.check(now) is None


def test_warmup_before_first_spike():
    det = SpikeDetector(window=10, mult=3.0, min_messages=15, warmup=60)
    feed(det, 0, 20, 1.0)
    now = feed(det, 20, 10, 10.0)
    assert det.check(now) is None  # not enough history to know the usual pace


def test_earlier_spike_does_not_raise_the_bar():
    det = SpikeDetector(window=10, mult=3.0, min_messages=15, cooldown=0)
    feed(det, 0, 100, 1.0)
    feed(det, 100, 10, 20.0)  # a huge burst in the history
    feed(det, 110, 100, 1.0)
    now = feed(det, 210, 10, 4.0)
    assert det.check(now) is not None  # the median ignores the old burst
