from __future__ import annotations

import statistics
from collections import deque
from dataclasses import dataclass


@dataclass(frozen=True)
class Spike:
    ts: float  # when the spike was detected
    ratio: float  # current chat rate / usual rate
    count: int  # messages in the current window


class SpikeDetector:
    """Detects moments when chat suddenly speeds up.

    The current rate is the number of messages in the last `window` seconds.
    The usual rate is the median over the preceding `baseline_seconds`, cut
    into buckets of the same length - a median, so an earlier spike does not
    raise the bar for the next one.
    """

    def __init__(
        self,
        window: float = 10,
        baseline_seconds: float = 300,
        mult: float = 3.0,
        min_messages: int = 15,
        cooldown: float = 90,
        warmup: float = 60,
    ) -> None:
        self.window = window
        self.baseline_seconds = baseline_seconds
        self.mult = mult
        self.min_messages = min_messages
        self.cooldown = cooldown
        self.warmup = warmup
        self._times: deque[float] = deque()
        self._started: float | None = None
        self._last_spike = float("-inf")

    def reset(self) -> None:
        self._times.clear()
        self._started = None

    def add(self, ts: float) -> None:
        if self._started is None:
            self._started = ts
        self._times.append(ts)
        horizon = ts - self.window - self.baseline_seconds
        while self._times and self._times[0] < horizon:
            self._times.popleft()

    def check(self, now: float) -> Spike | None:
        if self._started is None or now - self._last_spike < self.cooldown:
            return None
        window_start = now - self.window
        history = min(self.baseline_seconds, window_start - self._started)
        if history < self.warmup:
            return None

        current = sum(1 for t in self._times if t > window_start)
        if current < self.min_messages:
            return None

        n_buckets = int(history // self.window)
        buckets = [0] * n_buckets
        for t in self._times:
            if t > window_start:
                break
            idx = int((window_start - t) // self.window)
            if idx < n_buckets:
                buckets[idx] += 1
        usual = max(statistics.median(buckets), 1.0)

        ratio = current / usual
        if ratio < self.mult:
            return None
        self._last_spike = now
        return Spike(ts=now, ratio=ratio, count=current)
