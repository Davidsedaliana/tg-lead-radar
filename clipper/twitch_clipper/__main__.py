from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import shutil
import signal
import sys

from .bot import ClipBot
from .clipper import Clipper
from .config import Settings, load_dotenv, setup_console
from .judge import Transcriber, make_judge
from .store import Store

log = logging.getLogger("twitch_clipper")


async def main() -> None:
    setup_console()
    load_dotenv()
    cfg = Settings.from_env()

    if not cfg.bot_token:
        sys.exit("BOT_TOKEN is not set: copy .env.example to .env and fill it in")
    if shutil.which("ffmpeg") is None:
        sys.exit("ffmpeg is not installed (Ubuntu/Debian: apt install ffmpeg)")

    # Ctrl+C and `systemctl stop` both end the bot cleanly, child processes included.
    loop = asyncio.get_running_loop()
    current = asyncio.current_task()
    assert current is not None
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, current.cancel)

    store = Store(cfg.data_dir / "clipper.db")
    store.seed(cfg.streamers)
    try:
        judge = make_judge(cfg.judge_provider, cfg.judge_model, cfg.deepseek_base_url, os.environ)
    except ValueError as e:
        sys.exit(str(e))
    transcriber = (
        Transcriber(cfg.whisper_model, cfg.whisper_language) if cfg.whisper_model else None
    )
    clipper = Clipper(cfg, store, judge=judge, transcriber=transcriber)
    bot = ClipBot(cfg, store, statuses=clipper.statuses, on_change=clipper.sync)
    clipper.send_clip = bot.send_clip

    try:
        if not cfg.chat_id:
            log.warning("CHAT_ID is not set: send /start to the bot in the target chat")
            await bot.run()
            return
        log.info(
            "started: %d streamers, games: %s, scoring %s",
            len(store.streamers()),
            ", ".join(cfg.games) or "any",
            f"with {judge.model}" if judge else "off (no API key)",
        )
        await asyncio.gather(bot.run(), clipper.run())
    finally:
        await clipper.close()
        await bot.bot.session.close()


if __name__ == "__main__":
    with contextlib.suppress(asyncio.CancelledError, KeyboardInterrupt):
        asyncio.run(main())
