from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path

from .chat import ChatMessage, TwitchChat
from .config import Settings
from .detector import Spike, SpikeDetector
from .judge import Judge, Transcriber
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

    async def sync(self) -> None:
        """Start and stop recorders so they match the list in the database."""
        wanted = self.store.enabled_streamers()
        for name in set(self.recorders) - wanted:
            await self.recorders.pop(name).stop()
            self._detectors.pop(name, None)
            self._messages.pop(name, None)
            log.info("[%s] stopped watching", name)
        for name in wanted - set(self.recorders):
            source = self._source_factory(name) if self._source_factory else None
            probe = self._probe_factory(name) if self._probe_factory else None
            rec = Recorder(name, self.cfg, source_cmd=source, probe=probe)
            rec.start()
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
        await self.sync()
        await self.chat.run()

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
            task = asyncio.create_task(self._make_clip(msg.channel, spike))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

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
                    name, game, chat_lines, transcript, frames, spike.ratio, audio_mult
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
            if self.send_clip is not None:
                await self.send_clip(out, caption, clip_id, cfg.vertical)
            log.info("[%s] clip #%d sent", name, clip_id)
        finally:
            raw.unlink(missing_ok=True)
            out.unlink(missing_ok=True)
