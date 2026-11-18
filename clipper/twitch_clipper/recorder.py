from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import statistics
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from .config import Settings
from .media import mean_volume

log = logging.getLogger(__name__)

SILENCE_DB = -60.0


@dataclass(frozen=True)
class Segment:
    path: Path
    start: float  # wall-clock time when the segment started arriving
    end: float


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
    """

    def __init__(
        self,
        name: str,
        cfg: Settings,
        source_cmd: Callable[[], Sequence[str]] | None = None,
    ) -> None:
        self.name = name
        self.cfg = cfg
        self.dir = cfg.data_dir / "buffer" / name
        self._source_cmd = source_cmd or (lambda: streamlink_cmd(name, cfg.quality))
        self._levels: dict[str, float] = {}
        self._recording = False
        self._newest = 0.0  # mtime of the newest segment, refreshed every second
        self._task: asyncio.Task[None] | None = None

    # ---- lifecycle -------------------------------------------------------

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name=f"rec-{self.name}")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    @property
    def online(self) -> bool:
        return self._recording and time.time() - self._newest < 30

    async def _run(self) -> None:
        while True:
            try:
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
        try:
            try:
                source = await asyncio.create_subprocess_exec(
                    *self._source_cmd(),
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=write_fd,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                procs.append(source)
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
                log.info("[%s] stream ended", self.name)

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
