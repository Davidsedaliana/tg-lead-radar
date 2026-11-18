from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import statistics
import sys
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from .config import Settings
from .media import mean_volume

log = logging.getLogger(__name__)

SILENCE_DB = -60.0
OFFLINE_MARK = "No playable streams"  # what streamlink says about a streamer who is not live


@dataclass(frozen=True)
class Segment:
    path: Path
    start: float  # wall-clock time when the segment started arriving
    end: float


@dataclass(frozen=True)
class StreamInfo:
    category: str  # the game, as Twitch names it
    title: str


def game_allowed(category: str, games: Sequence[str]) -> bool:
    """An empty list allows everything; otherwise the category must match exactly."""
    return not games or category.casefold() in {g.casefold() for g in games}


def parse_probe(output: bytes) -> StreamInfo | None:
    """Read `streamlink --json`: stream metadata when live, an error object when offline."""
    try:
        data = json.loads(output)
    except ValueError:
        return None
    if not isinstance(data, dict) or "error" in data or not data.get("streams"):
        return None
    meta = data.get("metadata") or {}
    return StreamInfo(category=meta.get("category") or "", title=meta.get("title") or "")


def unexpected_error(output: bytes | str) -> str:
    """The last line of streamlink's complaint, unless it only says 'offline'."""
    if isinstance(output, bytes):
        output = output.decode("utf-8", errors="replace")
    with contextlib.suppress(ValueError, AttributeError):
        output = str(json.loads(output).get("error", ""))
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    if not lines or any(OFFLINE_MARK in line for line in lines):
        return ""
    return lines[-1][:300]


async def streamlink_probe(name: str) -> StreamInfo | None:
    """Ask Twitch whether the streamer is live and what they are playing."""
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "streamlink", "--json", f"https://www.twitch.tv/{name}",
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )  # fmt: skip
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=60)
    except (TimeoutError, asyncio.CancelledError):
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        await proc.wait()
        raise
    info = parse_probe(out)
    if info is None and (problem := unexpected_error(out)):
        raise RuntimeError(problem)
    return info


Probe = Callable[[], Awaitable[StreamInfo | None]]


def streamlink_cmd(name: str, quality: str) -> list[str]:
    return [
        sys.executable, "-m", "streamlink",
        "--stdout", "--loglevel", "error",
        "--retry-open", "2",
        f"https://www.twitch.tv/{name}", quality,
    ]  # fmt: skip


class Recorder:
    """Keeps the last few minutes of one stream on disk as short MPEG-TS segments.

    While the streamer is offline the source command exits right away and is
    retried every `offline_poll_seconds`, so the recorder doubles as the
    online check.

    With a list of games in the settings, the stream is recorded only while the
    streamer is in one of those Twitch categories: the category is checked
    before recording starts and every `category_poll_seconds` during it.
    """

    def __init__(
        self,
        name: str,
        cfg: Settings,
        source_cmd: Callable[[], Sequence[str]] | None = None,
        probe: Probe | None = None,
    ) -> None:
        self.name = name
        self.cfg = cfg
        self.dir = cfg.data_dir / "buffer" / name
        self._source_cmd = source_cmd or (lambda: streamlink_cmd(name, cfg.quality))
        if probe is None and cfg.games:
            probe = lambda: streamlink_probe(name)  # noqa: E731
        self._probe = probe
        self.info: StreamInfo | None = None  # what the last check saw; None when offline
        self._levels: dict[str, float] = {}
        self._recording = False
        self._last_problem = ""
        self._newest = 0.0  # mtime of the newest segment, refreshed every second
        self._task: asyncio.Task[None] | None = None

    # ---- lifecycle -------------------------------------------------------

    def start(self, delay: float = 0.0) -> None:
        """Start watching; `delay` spreads the checks of many streamers over time."""
        if self._task is None:
            self._task = asyncio.create_task(self._run(delay), name=f"rec-{self.name}")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    @property
    def online(self) -> bool:
        return self._recording and time.time() - self._newest < 30

    @property
    def wrong_game(self) -> bool:
        """Live, but in a category that is not on the list."""
        return self.info is not None and not game_allowed(self.info.category, self.cfg.games)

    async def _check(self) -> bool | None:
        """True: record. False: live, but another game. None: offline or the check failed."""
        assert self._probe is not None
        try:
            info = await self._probe()
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 - a failed check is the same as "unknown"
            log.warning("[%s] stream check failed: %s", self.name, e)
            return None
        if info is None:
            self.info = None
            return None
        if self.info is None or info.category != self.info.category:
            log.info("[%s] live, playing %s", self.name, info.category or "(no category)")
        self.info = info
        return game_allowed(info.category, self.cfg.games)

    async def _wait_for_other_game(self) -> None:
        """Returns when the streamer switches to a game that is not on the list."""
        while True:
            await asyncio.sleep(self.cfg.category_poll_seconds)
            # A failed check or 'offline' does not stop the recording: if the stream
            # really ended, the source process exits on its own.
            if await self._check() is False:
                log.info("[%s] switched to another game, recording paused", self.name)
                return

    async def _run(self, delay: float = 0.0) -> None:
        await asyncio.sleep(delay)
        while True:
            try:
                if self._probe is None or await self._check():
                    await self._record_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("[%s] recorder failed", self.name)
            await self._housekeep()
            await asyncio.sleep(self.cfg.offline_poll_seconds)

    async def _record_once(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        pattern = self.dir / f"{int(time.time())}_%06d.ts"
        read_fd, write_fd = os.pipe()
        procs: list[asyncio.subprocess.Process] = []
        watcher: asyncio.Task[None] | None = None
        complaints: asyncio.Task[bytes] | None = None
        try:
            try:
                source = await asyncio.create_subprocess_exec(
                    *self._source_cmd(),
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=write_fd,
                    stderr=asyncio.subprocess.PIPE,
                )
                procs.append(source)
                assert source.stderr is not None
                complaints = asyncio.create_task(source.stderr.read())
                ffmpeg = await asyncio.create_subprocess_exec(
                    "ffmpeg", "-hide_banner", "-loglevel", "error",
                    "-fflags", "+genpts", "-i", "pipe:0",
                    "-map", "0:v:0", "-map", "0:a:0?", "-c", "copy",
                    "-f", "segment", "-segment_time", str(self.cfg.segment_seconds),
                    "-segment_format", "mpegts", str(pattern),
                    stdin=read_fd,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )  # fmt: skip
                procs.append(ffmpeg)
            finally:
                os.close(read_fd)
                os.close(write_fd)

            self._recording = True
            watcher = asyncio.create_task(self._watch())
            waits = [asyncio.create_task(p.wait()) for p in procs]
            if self._probe is not None:
                waits.append(asyncio.create_task(self._wait_for_other_game()))
            try:
                await asyncio.wait(waits, return_when=asyncio.FIRST_COMPLETED)
            finally:
                for w in waits:
                    w.cancel()
        finally:
            was_live = self._recording and bool(self._files())
            self._recording = False
            if watcher is not None:
                watcher.cancel()
            for p in procs:
                await _terminate(p)
            if was_live:
                log.info("[%s] recording stopped", self.name)
            elif complaints is not None:
                # Nothing was recorded: say why, unless the streamer is simply offline.
                problem = unexpected_error(await complaints)
                if problem and problem != self._last_problem:
                    log.warning("[%s] cannot record: %s", self.name, problem)
                self._last_problem = problem

    async def _watch(self) -> None:
        announced = False
        while True:
            await asyncio.sleep(1)
            await self._housekeep()
            if not announced and self._files():
                announced = True
                log.info("[%s] is live, recording", self.name)

    # ---- segments --------------------------------------------------------

    def _files(self) -> list[tuple[float, Path]]:
        """All segment files as (mtime, path), oldest first."""
        out: list[tuple[float, Path]] = []
        for p in self.dir.glob("*.ts"):
            with contextlib.suppress(FileNotFoundError):
                out.append((p.stat().st_mtime, p))
        out.sort()
        return out

    def segments(self) -> list[Segment]:
        """Finished segments. The newest file is still being written while recording."""
        files = self._files()
        if self._recording:
            files = files[:-1]
        seg = self.cfg.segment_seconds
        result: list[Segment] = []
        prev_end: float | None = None
        for end, path in files:
            # A segment's content arrived between the previous file closing and this one.
            contiguous = prev_end is not None and 0 < end - prev_end < seg * 3
            start = prev_end if contiguous else end - seg
            result.append(Segment(path, start, end))
            prev_end = end
        return result

    def covering(self, start: float, end: float) -> list[Segment]:
        return [s for s in self.segments() if s.end > start and s.start < end]

    async def _housekeep(self) -> None:
        """Drop segments older than the buffer and measure loudness of new ones."""
        horizon = time.time() - self.cfg.buffer_seconds
        files = self._files()
        self._newest = files[-1][0] if files else 0.0
        for mtime, path in files:
            if mtime < horizon:
                path.unlink(missing_ok=True)
                self._levels.pop(path.name, None)
        for s in self.segments():
            if s.path.name not in self._levels:
                level = await mean_volume(s.path)
                self._levels[s.path.name] = SILENCE_DB if level is None else level

    def audio_ratio(self, start: float, end: float) -> float | None:
        """How much louder the loudest segment in [start, end] is than the usual level."""
        levels = {s.path.name: max(self._levels[s.path.name], SILENCE_DB)
                  for s in self.segments() if s.path.name in self._levels}  # fmt: skip
        if len(levels) < 5:
            return None
        inside = [levels[s.path.name] for s in self.covering(start, end) if s.path.name in levels]
        if not inside:
            return None
        usual = statistics.median(levels.values())
        return 10 ** ((max(inside) - usual) / 20)


async def _terminate(proc: asyncio.subprocess.Process) -> None:
    if proc.returncode is None:
        with contextlib.suppress(ProcessLookupError):
            proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=5)
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            await proc.wait()
