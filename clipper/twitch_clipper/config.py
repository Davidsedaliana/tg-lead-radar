from __future__ import annotations

import dataclasses
import os
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
    data_dir: Path = Path("data")

    # recording
    quality: str = "720p,720p60,480p,best"
    segment_seconds: int = 4
    buffer_seconds: int = 240
    offline_poll_seconds: int = 60

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
    timezone: str = "Europe/Moscow"

    # judge
    judge_enabled: bool = False
    judge_model: str = "claude-opus-5-5"
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
                values[f.name] = tuple(n for n in (normalize_login(x) for x in raw.split(",")) if n)
            else:
                values[f.name] = raw
        if "JUDGE_ENABLED" not in env:
            values["judge_enabled"] = bool(
                env.get("ANTHROPIC_API_KEY") or env.get("ANTHROPIC_AUTH_TOKEN")
            )
        return cls(**values)  # type: ignore[arg-type]
