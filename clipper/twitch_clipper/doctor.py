"""Check that everything the bot needs works on this computer.

    python -m twitch_clipper.doctor            # streamers from STREAMERS in .env
    python -m twitch_clipper.doctor buster     # or the ones you name

Goes through ffmpeg, streamlink, Twitch (stream, chat, a short real recording),
Telegram and the neural network, and prints one line per check.
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import random
import shutil
import sys
import tempfile
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

from . import chat as chat_module
from .config import Settings, load_dotenv, normalize_login, setup_console
from .judge import Moment, make_judge
from .media import concat_segments, render
from .recorder import Recorder, StreamInfo, streamlink_probe

Probe = Callable[[str], Awaitable[StreamInfo | None]]


class Skip(Exception):
    """The check does not apply (for example, no key is set)."""


async def _output(*cmd: str, timeout: float = 30) -> str:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(out.decode("utf-8", errors="replace").strip()[-300:] or "не запускается")
    return out.decode("utf-8", errors="replace")


async def check_python() -> str:
    return ".".join(map(str, sys.version_info[:3]))


async def check_ffmpeg() -> str:
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("не найден в PATH. Windows: winget install Gyan.FFmpeg и новое окно")
    version = (await _output("ffmpeg", "-version")).splitlines()[0]
    if "libx264" not in await _output("ffmpeg", "-hide_banner", "-encoders"):
        raise RuntimeError(f"{version}: в этой сборке нет кодека libx264, нужна полная сборка")
    return version.removeprefix("ffmpeg version ").split(" Copyright")[0]


async def check_streamlink() -> str:
    return (await _output(sys.executable, "-m", "streamlink", "--version")).strip()


async def find_live(names: list[str], probe: Probe, limit: int = 12) -> tuple[str, str]:
    """Check streamers until one is live. Returns (report line, login of a live one or '')."""
    names = names[:limit]
    results = await asyncio.gather(*(probe(n) for n in names), return_exceptions=True)
    errors = [r for r in results if isinstance(r, Exception)]
    if len(errors) == len(names):
        raise RuntimeError(f"Twitch не отвечает: {errors[0]}")
    live = [(n, r) for n, r in zip(names, results, strict=True) if isinstance(r, StreamInfo)]
    if not live:
        return f"Twitch отвечает, но из {len(names)} проверенных никто не в эфире", ""
    shown = ", ".join(f"{n} ({r.category or 'без категории'})" for n, r in live[:5])
    return f"в эфире {len(live)} из {len(names)}: {shown}", live[0][0]


async def check_chat(channel: str, seconds: float = 10) -> str:
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(chat_module.HOST, chat_module.PORT, ssl=True), timeout=20
    )
    try:
        nick = f"justinfan{random.randint(10000, 99999)}"
        writer.write(f"NICK {nick}\r\nJOIN #{channel}\r\n".encode())
        joined, messages = False, 0
        deadline = time.monotonic() + seconds
        while (left := deadline - time.monotonic()) > 0:
            try:
                raw = await asyncio.wait_for(reader.readline(), timeout=left)
            except TimeoutError:
                break
            if not raw:
                raise RuntimeError("сервер закрыл соединение")
            line = raw.decode("utf-8", errors="replace")
            joined = joined or f" JOIN #{channel}" in line or " 366 " in line
            messages += chat_module.parse_privmsg(line.rstrip("\r\n")) is not None
    finally:
        writer.close()
    if not joined:
        raise RuntimeError(f"подключился, но не смог войти в чат #{channel}")
    return f"вошёл в чат #{channel}, {messages} сообщений за {seconds:.0f} с"


async def check_recording(cfg: Settings, name: str, source_cmd=None, wait: float = 60) -> str:
    """Record a few real segments and build a vertical clip out of them."""
    folder = Path(tempfile.mkdtemp(prefix="clipper-doctor-"))
    rec = Recorder(name, dataclasses.replace(cfg, data_dir=folder, games=()), source_cmd=source_cmd)
    rec.start()
    try:
        deadline = time.monotonic() + wait
        while len(rec.segments()) < 3:
            if time.monotonic() > deadline:
                raise RuntimeError(f"за {wait:.0f} с не записалось ни одного куска видео")
            await asyncio.sleep(0.5)
        segments = rec.segments()
        raw, out = folder / "raw.ts", folder / "clip.mp4"
        await asyncio.to_thread(concat_segments, [s.path for s in segments], raw)
        await render(raw, out, 0, min(5.0, segments[-1].end - segments[0].start), cfg.vertical)
        size = out.stat().st_size
        if size < 10_000:
            raise RuntimeError("клип получился пустым")
        return f"{name}: записал {len(segments)} кусков, собрал клип на {size // 1024} КБ"
    finally:
        await rec.stop()
        shutil.rmtree(folder, ignore_errors=True)


async def check_telegram(cfg: Settings) -> str:
    if not cfg.bot_token:
        raise Skip("BOT_TOKEN не задан")
    from aiogram import Bot

    bot = Bot(cfg.bot_token)
    try:
        me = await bot.get_me()
        if not cfg.chat_id:
            return f"бот @{me.username} отвечает; CHAT_ID не задан, напиши боту /start в группе"
        chat = await bot.get_chat(cfg.chat_id)
        return f"бот @{me.username} видит чат «{chat.title or chat.full_name}»"
    finally:
        await bot.session.close()


async def check_judge(cfg: Settings, env) -> str:
    judge = make_judge(cfg.judge_provider, cfg.judge_model, cfg.deepseek_base_url, env)
    if judge is None:
        raise Skip("ключ API не задан, клипы пойдут без оценки")
    moment = Moment("test", "Grand Theft Auto VI", ["АХАХАХА", "что это было"], "", [], 3.0, None)
    verdict = await judge.rate(moment)
    if verdict is None:
        raise RuntimeError(f"{judge.model} не ответила, причина в строке WARNING выше")
    return f"{judge.model} отвечает (пробная оценка {verdict.score}/10)"


async def run(cfg: Settings, names: list[str], env, probe: Probe = streamlink_probe) -> bool:
    """Run every check, print the report, return True when nothing failed."""
    failed = 0
    live = ""

    async def step(title: str, check: Callable[[], Awaitable[str]]) -> str:
        nonlocal failed
        try:
            detail = await check()
            mark = "[  OK  ]"
        except Skip as e:
            detail, mark = str(e), "[  --  ]"
        except Exception as e:  # noqa: BLE001 - every failure belongs in the report
            detail, mark = f"{type(e).__name__}: {e}"[:400], "[ОШИБКА]"
            failed += 1
        print(f"{mark} {title}: {detail}", flush=True)
        return detail if mark == "[  OK  ]" else ""

    await step("Python", check_python)
    ffmpeg_ok = await step("ffmpeg", check_ffmpeg)
    streamlink_ok = await step("streamlink", check_streamlink)

    async def twitch() -> str:
        nonlocal live
        detail, live = await find_live(names, probe)
        return detail

    if streamlink_ok:
        await step("Twitch, эфиры", twitch)
    await step("Twitch, чат", lambda: check_chat(live or names[0]))
    if live and ffmpeg_ok:
        await step("Запись и сборка клипа", lambda: check_recording(cfg, live))
    else:
        print("[  --  ] Запись и сборка клипа: некого записывать, никто не в эфире", flush=True)
    await step("Telegram", lambda: check_telegram(cfg))
    await step("Нейросеть", lambda: check_judge(cfg, env))

    print("\nВсё работает." if not failed else f"\nНе работает пунктов: {failed}.", flush=True)
    return not failed


def main() -> None:
    import os

    parser = argparse.ArgumentParser(prog="python -m twitch_clipper.doctor", description=__doc__)
    parser.add_argument("streamers", nargs="*", help="логины Twitch (по умолчанию из STREAMERS)")
    args = parser.parse_args()
    setup_console()
    load_dotenv()
    cfg = Settings.from_env()
    names = [normalize_login(n) for n in args.streamers] or list(cfg.streamers)
    if not names:
        parser.error("укажи стримера или заполни STREAMERS в .env")
    ok = asyncio.run(run(cfg, names, os.environ))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
