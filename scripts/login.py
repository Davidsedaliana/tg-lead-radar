"""Interactive one-time login for userbot mode.

Run it yourself in a terminal — Telegram sends a confirmation code that only
you can enter:

    .venv\\Scripts\\python scripts/login.py

The session is stored next to the database, so afterwards the radar starts
without asking anything. Also reports which of the configured channels and
chats the account can actually read.

scripts/discover_channels.py runs on a session of its own, so it works while
the radar holds the main one. Log it in once the same way:

    .venv\\Scripts\\python scripts/login.py --session data/discover
"""

import argparse
import asyncio
import sys
from pathlib import Path

from telethon import TelegramClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tg_lead_radar.config import load_settings  # noqa: E402


async def main() -> int:
    settings = load_settings()
    parser = argparse.ArgumentParser(description="Одноразовый вход в Telegram")
    parser.add_argument(
        "--session",
        default=settings.session_path,
        help="файл сессии без .session (по умолчанию SESSION_PATH из .env)",
    )
    args = parser.parse_args()
    if not settings.api_id or not settings.api_hash:
        print("Нет API_ID / API_HASH в .env — возьмите их на my.telegram.org")
        return 2

    Path(args.session).parent.mkdir(parents=True, exist_ok=True)
    client = TelegramClient(args.session, settings.api_id, settings.api_hash)
    await client.start()

    me = await client.get_me()
    print(f"\nВошли как {me.first_name} (@{me.username or me.id})\n")

    ok, missing = [], []
    for name in settings.channel_list():
        try:
            entity = await client.get_entity(name)
        except Exception as exc:
            missing.append(f"@{name}: {type(exc).__name__}")
        else:
            ok.append(f"@{name} — {getattr(entity, 'title', '')}")

    print("Доступны:")
    for line in ok:
        print("  ✓", line)
    if missing:
        print("\nНедоступны (вступите в чат и повторите):")
        for line in missing:
            print("  ✗", line)

    await client.disconnect()
    print("\nСессия сохранена. Запуск юзербота: .venv\\Scripts\\python -m tg_lead_radar")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
