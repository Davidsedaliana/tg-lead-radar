"""Jobs mode CLI. Reads data/jobs.db only — no Telegram, so it runs while
the radar is up:

    python -m tg_lead_radar.jobs export               # everything
    python -m tg_lead_radar.jobs export --since-last  # new since the last such run

--since-last is the flag hh-vacancy-scraper uses too; --since still works.
"""

import argparse

from ..config import load_settings
from .export import export
from .store import JobsStore


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tg_lead_radar.jobs")
    commands = parser.add_subparsers(dest="command", required=True)
    cmd = commands.add_parser("export", help="выгрузить вакансии в jsonl и дневные дайджесты")
    cmd.add_argument(
        "--since-last",
        "--since",
        dest="since",
        action="store_true",
        help="только новое и обновлённое с прошлого запуска с этим флагом",
    )
    cmd.add_argument("--out", help="папка выгрузки (по умолчанию JOBS_EXPORT_DIR из .env)")
    args = parser.parse_args(argv)

    settings = load_settings()
    store = JobsStore(settings.jobs_db_path)
    try:
        result = export(store, args.out or settings.jobs_export_dir, since=args.since)
    finally:
        store.close()
    scope = "новых и обновлённых" if args.since else "всего"
    print(f"Вакансий {scope}: {result.vacancies} → {result.jsonl}")
    for path in result.digests:
        print(f"  дайджест: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
