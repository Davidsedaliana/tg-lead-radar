"""Find new live channels with QA vacancies and write a report — nothing else.

Candidates come from Telegram's global search (queries in jobs.toml) and
from "similar channels" of every channel in the vacancy folder. Channels you
are already in are skipped. Each candidate's last 100 posts give freshness,
activity, views and the share of QA vacancies; the report lists them by
score. No channel is joined: subscribing is up to you.

Runs on its own session, so the radar can keep running. Log it in once:

    .venv\\Scripts\\python scripts/login.py --session data/discover
    .venv\\Scripts\\python scripts/discover_channels.py
"""

import argparse
import asyncio
import dataclasses
import logging
import sys
from pathlib import Path

from telethon import TelegramClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tg_lead_radar.config import load_settings  # noqa: E402
from tg_lead_radar.jobs.collector import resolve_jobs_sources  # noqa: E402
from tg_lead_radar.jobs.config import load_jobs_config  # noqa: E402
from tg_lead_radar.jobs.discover import discover, subscribed_ids, write_reports  # noqa: E402
from tg_lead_radar.web import acquire_single_instance_lock  # noqa: E402


def _session_file(session: str) -> Path:
    return Path(session if session.endswith(".session") else session + ".session")


async def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = load_settings()
    parser = argparse.ArgumentParser(description="Поиск новых каналов с вакансиями QA")
    parser.add_argument(
        "--session",
        default=settings.discover_session_path,
        help="сессия Telegram (по умолчанию DISCOVER_SESSION_PATH из .env)",
    )
    parser.add_argument("--out", default=settings.jobs_export_dir, help="папка для отчёта")
    parser.add_argument("--max-candidates", type=int, help="сколько кандидатов проверить")
    args = parser.parse_args()

    if not settings.api_id or not settings.api_hash:
        print("Нет API_ID / API_HASH в .env — возьмите их на my.telegram.org")
        return 2
    lock = None
    if _session_file(args.session) == _session_file(settings.session_path):
        # The radar's own session: refuse while the radar holds it.
        lock = acquire_single_instance_lock(str(Path(settings.db_path).with_name("radar.lock")))
    if not _session_file(args.session).exists():
        print(f"Сессии {args.session} ещё нет. Войдите один раз:\n"
              f"  python scripts/login.py --session {args.session}")
        return 2

    config = load_jobs_config(settings.jobs_config_path)
    if args.max_candidates:
        config = dataclasses.replace(
            config,
            discover=dataclasses.replace(config.discover, max_candidates=args.max_candidates),
        )

    client = TelegramClient(args.session, settings.api_id, settings.api_hash)
    await client.connect()
    try:
        if not await client.is_user_authorized():
            print(f"Сессия {args.session} не авторизована: "
                  f"python scripts/login.py --session {args.session}")
            return 2
        sources = await resolve_jobs_sources(client, settings)
        print(f"Каналов в папке «{settings.jobs_folder}» и JOBS_CHANNELS: {len(sources)}")
        result = await discover(client, config, sources, await subscribed_ids(client))
    finally:
        await client.disconnect()
        if lock:
            lock.close()

    md, table = write_reports(result, config.discover, args.out)
    print(f"Кандидатов: {result.candidates_total}, проверено: {len(result.stats)}")
    if result.stopped:
        print(f"⚠️ {result.stopped}")
    print(f"Отчёт: {md}\n       {table}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
