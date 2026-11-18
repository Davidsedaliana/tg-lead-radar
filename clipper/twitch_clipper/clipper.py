from __future__ import annotations

import asyncio
import contextlib
import logging
import shutil
import time
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from .chat import ChatMessage, TwitchChat
from .config import Settings
from .detector import Spike, SpikeDetector
from .judge import Judge, Moment, Transcriber
from .media import concat_segments, grab_frames, render
from .recorder import Probe, Recorder
from .store import Store
from .text import build_caption, guess_category, sample_chat

log = logging.getLogger(__name__)

SendClip = Callable[[Path, str, int, bool], Awaitable[None]]
SourceFactory = Callable[[str], Callable[[], Sequence[str]]]
ProbeFactory = Callable[[str], Probe]

# What /list shows for a streamer: (state, game). States: "recording", "other_game", "offline".
Status = tuple[str, str]

CHAT_MEMORY_SECONDS = 180

# Trim buttons: how many seconds each one moves the start and the end of the clip.
TRIM_STEPS = {"s-10": (-10.0, 0.0), "s+5": (5.0, 0.0), "e-5": (0.0, -5.0), "e+10": (0.0, 10.0)}
MIN_CLIP_SECONDS = 5.0


def remove(path: Path) -> None:
    """Delete a file; a file that is busy or already gone is not worth crashing over."""
    with contextlib.suppress(OSError):
        path.unlink(missing_ok=True)


class TrimError(Exception):
    """Why a clip cannot be trimmed; the text is shown to the person who pressed the button."""


@dataclass(frozen=True)
class TrimPlan:
    clip_id: int
    source: Path
    source_start: float
    start: float
    end: float
    caption: str

    @property
    def duration(self) -> float:
        return self.end - self.start


class Clipper:
    """Ties everything together: chat -> spike -> cut -> score -> send."""

    def __init__(
        self,
        cfg: Settings,
        store: Store,
        send_clip: SendClip | None = None,
        judge: Judge | None = None,
        transcriber: Transcriber | None = None,
        source_factory: SourceFactory | None = None,
        probe_factory: ProbeFactory | None = None,
    ) -> None:
        self.cfg = cfg
        self.store = store
        self.send_clip = send_clip
        self.judge = judge
        self.transcriber = transcriber
        self._source_factory = source_factory
        self._probe_factory = probe_factory
        self.chat = TwitchChat(self.on_message)
        self.recorders: dict[str, Recorder] = {}
        self._detectors: dict[str, SpikeDetector] = {}
        self._messages: dict[str, deque[tuple[float, str]]] = {}
        self._render_slots = asyncio.Semaphore(cfg.max_parallel_renders)
        self._tasks: set[asyncio.Task[None]] = set()
        self._trimming: set[int] = set()

    # ---- streamers -------------------------------------------------------

    def online(self) -> set[str]:
        return {name for name, rec in self.recorders.items() if rec.online}

    def statuses(self) -> dict[str, Status]:
        result: dict[str, Status] = {}
        for name, rec in self.recorders.items():
            game = rec.info.category if rec.info else ""
            if rec.online:
                result[name] = ("recording", game)
            elif rec.wrong_game:
                result[name] = ("other_game", game)
            else:
                result[name] = ("offline", "")
        return result

    def chat_stats(self, name: str) -> tuple[int, float] | None:
        """(messages in the last window, usual number); None while there is no baseline yet."""
        detector = self._detectors.get(name)
        return detector.stats(time.time()) if detector else None

    async def sync(self) -> None:
        """Start and stop recorders so they match the list in the database."""
        wanted = self.store.enabled_streamers()
        for name in set(self.recorders) - wanted:
            await self.recorders.pop(name).stop()
            self._detectors.pop(name, None)
            self._messages.pop(name, None)
            log.info("[%s] stopped watching", name)
        new = sorted(wanted - set(self.recorders))
        # Every streamer is checked once per offline_poll_seconds. Starting them a little
        # apart keeps those checks from all landing in the same second.
        spacing = min(2.0, self.cfg.offline_poll_seconds / max(len(new), 1))
        for i, name in enumerate(new):
            source = self._source_factory(name) if self._source_factory else None
            probe = self._probe_factory(name) if self._probe_factory else None
            rec = Recorder(name, self.cfg, source_cmd=source, probe=probe)
            rec.start(delay=i * spacing)
            self.recorders[name] = rec
            self._detectors[name] = SpikeDetector(
                window=self.cfg.chat_window,
                mult=self.cfg.chat_mult,
                min_messages=self.cfg.chat_min_messages,
                cooldown=self.cfg.cooldown_seconds,
                warmup=self.cfg.chat_warmup_seconds,
            )
            self._messages[name] = deque()
            log.info("[%s] watching", name)
        self.chat.set_channels(wanted)

    async def run(self) -> None:
        await asyncio.to_thread(self.clean_leftovers)
        await self.sync()
        self._spawn(self._janitor())
        await self.chat.run()

    def clean_leftovers(self) -> None:
        """On start, remove what a previous run left behind (a crash, a power cut)."""
        data = self.cfg.data_dir
        # Buffers and half-made clips of the previous run are useless after a restart.
        shutil.rmtree(data / "buffer", ignore_errors=True)
        shutil.rmtree(data / "clips", ignore_errors=True)
        # Sources that no clip in the database points to any more.
        known = {Path(p).resolve() for p in self.store.source_paths()}
        for path in (data / "sources").glob("*.ts"):
            if path.resolve() not in known:
                remove(path)

    def _spawn(self, coro: Awaitable[None]) -> None:
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _janitor(self) -> None:
        while True:
            await asyncio.to_thread(self.clean_sources)
            await asyncio.sleep(600)

    def clean_sources(self) -> None:
        """Delete clip sources that are too old, then the oldest ones if they take too much."""
        cutoff = time.time() - self.cfg.keep_source_hours * 3600
        for old in self.store.pop_old_sources(cutoff):
            remove(Path(old))

        files: list[tuple[float, int, Path]] = []
        for path in (self.cfg.data_dir / "sources").glob("*.ts"):
            with contextlib.suppress(OSError):
                stat = path.stat()
                files.append((stat.st_mtime, stat.st_size, path))
        files.sort()
        total = sum(size for _, size, _ in files)
        limit = self.cfg.sources_max_gb * 1024**3
        for _, size, path in files:
            if total <= limit:
                break
            self.store.forget_source(path)
            remove(path)
            total -= size

    async def close(self) -> None:
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        await asyncio.gather(*(rec.stop() for rec in self.recorders.values()))
        self.recorders.clear()

    # ---- detection -------------------------------------------------------

    def on_message(self, msg: ChatMessage) -> None:
        rec = self.recorders.get(msg.channel)
        detector = self._detectors.get(msg.channel)
        if rec is None or detector is None:
            return
        if not rec.online:
            # Offline chat is not interesting, and the usual rate has to be learned anew.
            detector.reset()
            return
        memory = self._messages[msg.channel]
        memory.append((msg.ts, msg.text))
        while memory and memory[0][0] < msg.ts - CHAT_MEMORY_SECONDS:
            memory.popleft()
        detector.add(msg.ts)
        spike = detector.check(msg.ts)
        if spike is not None:
            log.info(
                "[%s] chat spike x%.1f (%d messages in %ds)",
                msg.channel, spike.ratio, spike.count, self.cfg.chat_window,
            )  # fmt: skip
            self._spawn(self._make_clip(msg.channel, spike))

    # ---- clip ------------------------------------------------------------

    async def _make_clip(self, name: str, spike: Spike) -> None:
        try:
            await self._make_clip_inner(name, spike)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("[%s] clip failed", name)

    async def _make_clip_inner(self, name: str, spike: Spike) -> None:
        cfg = self.cfg
        start, end = spike.ts - cfg.pre_seconds, spike.ts + cfg.post_seconds
        # Wait for the "after" part to be recorded and its segments to be closed.
        await asyncio.sleep(max(0.0, end - time.time()) + cfg.segment_seconds * 2 + 2)

        rec = self.recorders.get(name)
        segments = rec.covering(start, end) if rec else []
        if rec is None or not segments:
            log.info("[%s] no video for the spike, skipped", name)
            return
        clip_start = max(start, segments[0].start)
        duration = min(end, segments[-1].end) - clip_start
        if duration < (cfg.pre_seconds + cfg.post_seconds) / 2:
            log.info("[%s] only %.0fs of video for the spike, skipped", name, duration)
            return

        audio_mult = rec.audio_ratio(spike.ts - cfg.chat_window - 5, spike.ts + 5)
        if cfg.audio_min_mult and audio_mult is not None and audio_mult < cfg.audio_min_mult:
            log.info("[%s] sound only x%.1f, skipped", name, audio_mult)
            return

        texts = [
            text
            for ts, text in self._messages.get(name, ())
            if spike.ts - cfg.chat_window - 5 <= ts <= end
        ]
        chat_lines = sample_chat(texts)

        work = cfg.data_dir / "clips"
        work.mkdir(parents=True, exist_ok=True)
        raw = work / f"{name}_{int(spike.ts)}.ts"
        out = raw.with_suffix(".mp4")
        try:
            await asyncio.to_thread(concat_segments, [s.path for s in segments], raw)
            offset = clip_start - segments[0].start
            async with self._render_slots:
                await render(raw, out, offset, duration, cfg.vertical)

            verdict = None
            if self.judge is not None:
                frames = await grab_frames(raw, offset, duration)
                transcript = await self.transcriber.transcribe(out) if self.transcriber else ""
                game = rec.info.category if rec.info else ""
                verdict = await self.judge.rate(
                    Moment(name, game, chat_lines, transcript, frames, spike.ratio, audio_mult)
                )
                if verdict is not None and verdict.score < cfg.min_score:
                    log.info("[%s] scored %d/10 (%s), not sent", name, verdict.score,
                             verdict.summary)  # fmt: skip
                    return

            category = verdict.category if verdict else guess_category(texts)
            score = verdict.score if verdict else None
            summary = verdict.summary if verdict else ""
            clip_id = self.store.add_clip(name, spike.ratio, audio_mult, score, category, summary)
            caption = build_caption(
                name, spike.ts, cfg.timezone, category, spike.ratio, audio_mult,
                score, summary, chat_lines,
            )  # fmt: skip
            clip_end = clip_start + duration
            self.store.set_clip_range(clip_id, clip_start, clip_end, caption)
            if self.send_clip is not None:
                await self.send_clip(out, caption, clip_id, cfg.vertical)
            log.info("[%s] clip #%d sent", name, clip_id)
            if cfg.keep_source_hours > 0:
                self._spawn(self._save_source(clip_id, rec, clip_start, clip_end))
        finally:
            remove(raw)
            remove(out)

    # ---- trim buttons ----------------------------------------------------

    async def _save_source(self, clip_id: int, rec: Recorder, start: float, end: float) -> None:
        """Keep a wider recording around the clip so that it can be re-cut later."""
        cfg = self.cfg
        wide_start, wide_end = start - cfg.trim_margin_before, end + cfg.trim_margin_after
        try:
            # The clip is already sent; the extra seconds after it are still being recorded.
            await asyncio.sleep(max(0.0, wide_end - time.time()) + cfg.segment_seconds * 2 + 2)
            segments = rec.covering(wide_start, wide_end)
            if not segments:
                return
            folder = cfg.data_dir / "sources"
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / f"{clip_id}.ts"
            await asyncio.to_thread(concat_segments, [s.path for s in segments], path)
            self.store.set_source(clip_id, path, segments[0].start, segments[-1].end)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("clip #%d: source not saved, trim buttons will not work", clip_id)

    def plan_trim(self, clip_id: int, action: str) -> TrimPlan:
        """Work out the new boundaries for a pressed trim button, or say why it cannot be done."""
        clip = self.store.get_clip(clip_id)
        if clip is None or clip.start is None or clip.end is None or action not in TRIM_STEPS:
            raise TrimError("Этот клип подрезать нельзя")
        if clip_id in self._trimming:
            raise TrimError("Уже режу этот клип, подожди")
        if not clip.source_path or clip.source_start is None or clip.source_end is None:
            if time.time() - clip.end < self.cfg.trim_margin_after + 60:
                raise TrimError("Исходник ещё дописывается, нажми через полминуты")
            raise TrimError("Исходник этого клипа не сохранился")
        source = Path(clip.source_path)
        if not source.is_file():
            raise TrimError(f"Исходник уже удалён: он хранится {self.cfg.keep_source_hours} ч")

        move_start, move_end = TRIM_STEPS[action]
        start = max(clip.source_start, clip.start + move_start)
        end = min(clip.source_end, clip.end + move_end)
        if end - start < MIN_CLIP_SECONDS:
            raise TrimError("Короче уже некуда")
        if abs(start - clip.start) < 0.5 and abs(end - clip.end) < 0.5:
            raise TrimError("Дальше в эту сторону записи нет")
        return TrimPlan(clip_id, source, clip.source_start, start, end, clip.caption)

    @contextlib.asynccontextmanager
    async def trimmed(self, plan: TrimPlan) -> AsyncIterator[Path]:
        """Render the re-cut clip. The new boundaries are saved only if the caller succeeds."""
        if plan.clip_id in self._trimming:
            raise TrimError("Уже режу этот клип, подожди")
        self._trimming.add(plan.clip_id)
        work = self.cfg.data_dir / "clips"
        work.mkdir(parents=True, exist_ok=True)
        out = work / f"trim_{plan.clip_id}_{int(time.time() * 1000)}.mp4"
        try:
            async with self._render_slots:
                await render(
                    plan.source, out, plan.start - plan.source_start, plan.duration,
                    self.cfg.vertical,
                )  # fmt: skip
            yield out
            self.store.set_clip_range(plan.clip_id, plan.start, plan.end)
        finally:
            self._trimming.discard(plan.clip_id)
            remove(out)
