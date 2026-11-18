from __future__ import annotations

import contextlib
import dataclasses
import logging
import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


def load_dotenv(path: str | Path = ".env") -> None:
    """Minimal .env loader: KEY=VALUE lines, existing variables win."""
    p = Path(path)
    if not p.is_file():
        return
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip().strip("'\"")
        os.environ.setdefault(key.strip(), value)


def setup_console() -> None:
    """Logging plus UTF-8 output: Windows consoles choke on emoji and Cyrillic otherwise."""
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(AttributeError, ValueError):
            stream.reconfigure(encoding="utf-8", errors="replace")
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    logging.getLogger("aiogram.event").setLevel(logging.WARNING)


def normalize_login(name: str) -> str:
    """'https://twitch.tv/Buster ' -> 'buster'."""
    name = name.strip().lower().rstrip("/")
    name = name.rsplit("/", 1)[-1]
    return name.lstrip("@#")


@dataclass(frozen=True)
class Settings:
    bot_token: str = ""
    chat_id: int = 0
    streamers: tuple[str, ...] = ()
    # Twitch categories to cut clips from, exact names; empty means any game.
    games: tuple[str, ...] = ()
    data_dir: Path = Path("data")

    # recording
    quality: str = "720p,720p60,480p,best"
    segment_seconds: int = 4
    buffer_seconds: int = 240
    offline_poll_seconds: int = 60
    category_poll_seconds: int = 120

    # detector
    chat_window: int = 10
    chat_mult: float = 3.0
    chat_min_messages: int = 15
    chat_warmup_seconds: int = 60
    cooldown_seconds: int = 90
    audio_min_mult: float = 0.0

    # clip
    pre_seconds: int = 30
    post_seconds: int = 15
    vertical: bool = True
    max_parallel_renders: int = 1
    # Trim buttons: how much extra video around the clip is kept, and for how long.
    trim_margin_before: int = 30
    trim_margin_after: int = 30
    keep_source_hours: int = 24  # 0 = keep nothing, no trim buttons
    timezone: str = "Europe/Moscow"

    # judge
    # "deepseek", "anthropic" or "" (no scoring). Unset: picked by which API key is present.
    judge_provider: str = ""
    judge_model: str = ""  # empty: the provider's default
    deepseek_base_url: str = "https://api.deepseek.com"
    min_score: int = 6
    whisper_model: str = ""
    whisper_language: str = "ru"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        env = os.environ if env is None else env
        values: dict[str, object] = {}
        for f in dataclasses.fields(cls):
            raw = env.get(f.name.upper())
            if raw is None or raw.strip() == "":
                continue
            raw = raw.strip()
            default = f.default
            if isinstance(default, bool):
                values[f.name] = raw.lower() in ("1", "true", "yes", "on")
            elif isinstance(default, int):
                values[f.name] = int(raw)
            elif isinstance(default, float):
                values[f.name] = float(raw)
            elif isinstance(default, Path):
                values[f.name] = Path(raw)
            elif isinstance(default, tuple):
                clean = normalize_login if f.name == "streamers" else str.strip
                values[f.name] = tuple(n for n in (clean(x) for x in raw.split(",")) if n)
            else:
                values[f.name] = raw
        if "judge_provider" not in values:
            if env.get("DEEPSEEK_API_KEY"):
                values["judge_provider"] = "deepseek"
            elif env.get("ANTHROPIC_API_KEY") or env.get("ANTHROPIC_AUTH_TOKEN"):
                values["judge_provider"] = "anthropic"
        return cls(**values)  # type: ignore[arg-type]
