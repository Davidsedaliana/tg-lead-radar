"""Try the pipeline on real streams without Telegram.

    python -m twitch_clipper.tryout buster evelone192 --minutes 30

Records the streams, watches chat, and saves every clip with its caption to
data/out/. Every 10 seconds prints what it sees: who is live, which game, and
how busy the chat is compared with the usual pace.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import dataclasses
import logging
import os
import shutil
import sys
import time
from pathlib import Path

from .clipper import Clipper, ProbeFactory, SourceFactory
from .config import Settings, load_dotenv, normalize_login, setup_console
from .judge import Transcriber, make_judge
from .store import Store

log = logging.getLogger("twitch_clipper.tryout")

REPORT_EVERY = 10


def status_line(clipper: Clipper, name: str, window: int) -> str:
    state, game = clipper.statuses().get(name, ("offline", ""))
    if state == "offline":
        return f"{name:<16} не в эфире"
    if state == "other_game":
        return f"{name:<16} в эфире, но другая игра: {game or 'без категории'}"
    line = f"{name:<16} пишу · {game or 'без категории'} · чат: "
    stats = clipper.chat_stats(name)
    if stats is None:
        return line + "коплю статистику"
    current, usual = stats
    return line + f"{current} за {window}с (обычно {usual:.0f}, x{current / usual:.1f})"


async def run(
    cfg: Settings,
    names: list[str],
    minutes: float,
    source_factory: SourceFactory | None = None,
    probe_factory: ProbeFactory | None = None,
) -> list[Path]:
    out_dir = cfg.data_dir / "out"
    out_dir.mkdir(parents=True, exist_ok=True)
    saved: list[Path] = []

    async def save_clip(path: Path, caption: str, clip_id: int, vertical: bool) -> None:
        target = out_dir / f"{clip_id:03d}_{path.name}"
        await asyncio.to_thread(shutil.copy, path, target)
        target.with_suffix(".txt").write_text(caption + "\n", encoding="utf-8")
        saved.append(target)
        print(f"\n=== КЛИП СОХРАНЁН: {target}\n{caption}\n", flush=True)

    store = Store(":memory:")
    for name in names:
        store.add_streamer(name)
    judge = make_judge(cfg.judge_provider, cfg.judge_model, cfg.deepseek_base_url, os.environ)
    transcriber = (
        Transcriber(cfg.whisper_model, cfg.whisper_language) if cfg.whisper_model else None
    )
    clipper = Clipper(
        cfg,
        store,
        send_clip=save_clip,
        judge=judge,
        transcriber=transcriber,
        source_factory=source_factory,
        probe_factory=probe_factory,
    )
    print(
        f"Стримеры: {', '.join(names)}\n"
        f"Игры: {', '.join(cfg.games) or 'любые'}\n"
        f"Оценка: {judge.model if judge else 'выключена (нет ключа API)'}\n"
        f"Клипы: {out_dir}\n"
        f"Порог: чат x{cfg.chat_mult} и минимум {cfg.chat_min_messages} сообщений"
        f" за {cfg.chat_window}с\n",
        flush=True,
    )

    worker = asyncio.create_task(clipper.run())
    deadline = time.monotonic() + minutes * 60
    try:
        while time.monotonic() < deadline and not worker.done():
            await asyncio.sleep(min(REPORT_EVERY, max(0.1, deadline - time.monotonic())))
            stamp = time.strftime("%H:%M:%S")
            for name in names:
                print(f"{stamp} {status_line(clipper, name, cfg.chat_window)}", flush=True)
        if worker.done():
            worker.result()  # surface the error that stopped the pipeline
    finally:
        worker.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await worker
        await clipper.close()
    print(f"\nГотово. Клипов сохранено: {len(saved)} ({out_dir})", flush=True)
    return saved


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="python -m twitch_clipper.tryout",
        description="Проба на настоящих стримах без Telegram: клипы сохраняются в data/out/",
    )
    parser.add_argument("streamers", nargs="*", help="логины Twitch (по умолчанию из STREAMERS)")
    parser.add_argument("--minutes", type=float, default=30, help="сколько работать (30)")
    parser.add_argument(
        "--any-game", action="store_true", help="не смотреть на GAMES, резать любую игру"
    )
    args = parser.parse_args()

    setup_console()
    load_dotenv()
    cfg = Settings.from_env()
    cfg = dataclasses.replace(cfg, keep_source_hours=0)  # no buttons here, nothing to re-cut
    if args.any_game:
        cfg = dataclasses.replace(cfg, games=())
    names = [normalize_login(n) for n in args.streamers] or list(cfg.streamers)
    if not names:
        parser.error("укажи хотя бы одного стримера: python -m twitch_clipper.tryout buster")
    if shutil.which("ffmpeg") is None:
        sys.exit("ffmpeg не установлен или не виден в PATH")

    try:
        asyncio.run(run(cfg, names, args.minutes))
    except ValueError as e:
        sys.exit(str(e))
    except KeyboardInterrupt:
        print("\nОстановлено.")


if __name__ == "__main__":
    main()
