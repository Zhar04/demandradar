"""CLI DemandRadar.

Примеры:
  python -m demandradar --once --dry-run              # один проход конвейера
  python -m demandradar --once --connector goszakup --backfill 7
  python -m demandradar --serve --port 8080           # веб-дашборд
  python -m demandradar --digest --dry-run            # дневной дайджест
Режим демона (24/7) появится на Этапе 7.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import demandradar.connectors  # noqa: F401 — регистрирует коннекторы
from demandradar.config import PROJECT_ROOT, load_settings
from demandradar.connectors.base import all_connector_keys
from demandradar.core.pipeline import run_once


def setup_logging(level: str) -> None:
    log_dir = PROJECT_ROOT / "logs"
    log_dir.mkdir(exist_ok=True)
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(log_dir / "demandradar.log", encoding="utf-8"),
        ],
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="demandradar", description="Радар спроса КЗ/СНГ")
    parser.add_argument("--once", action="store_true", help="один проход конвейера и выход")
    parser.add_argument("--serve", action="store_true", help="запустить веб-дашборд")
    parser.add_argument("--digest", action="store_true", help="отправить дневной дайджест и выйти")
    parser.add_argument("--port", type=int, default=8080, help="порт дашборда (с --serve)")
    parser.add_argument("--dry-run", action="store_true", help="не отправлять уведомления (печать в консоль)")
    parser.add_argument("--connector", help="запустить только указанный коннектор")
    parser.add_argument("--backfill", type=int, metavar="N", help="собрать за последние N дней (игнорируя курсор)")
    parser.add_argument("--db", type=Path, help="переопределить путь к БД")
    args = parser.parse_args(argv)

    settings = load_settings()
    if args.db:
        settings.db_path = args.db
    setup_logging(settings.log_level)

    if args.serve:
        import uvicorn

        from demandradar.dashboard.app import create_app

        uvicorn.run(create_app(settings), host="127.0.0.1", port=args.port, log_level="info")
        return 0

    if args.digest:
        from demandradar.net.http import Fetcher
        from demandradar.notify.digest import build_digest
        from demandradar.notify.telegram import TelegramNotifier
        from demandradar.storage.db import Database
        from demandradar.storage.repo import SignalRepository

        with Database(settings.db_path) as db:
            db.migrate()
            text = build_digest(SignalRepository(db))
        notifier = TelegramNotifier(
            Fetcher(),
            bot_token=settings.telegram_bot_token,
            chat_id=settings.telegram_chat_id,
            dry_run=args.dry_run,
        )
        return 0 if notifier.send_text(text) else 1

    if not args.once:
        parser.error("укажите режим: --once, --serve или --digest (демон появится на Этапе 7)")

    keys = None
    if args.connector:
        known = all_connector_keys()
        if args.connector not in known:
            parser.error(f"неизвестный коннектор {args.connector!r}; доступны: {', '.join(known)}")
        keys = [args.connector]

    since = None
    if args.backfill:
        since = datetime.now(UTC) - timedelta(days=args.backfill)

    report = run_once(settings, connector_keys=keys, dry_run=args.dry_run, since=since)

    print("\n=== Итог прохода ===")
    for line in report.summary_lines():
        print(line)
    print(f"Новых сигналов: {report.total_new}")

    has_errors = any(s.error for s in report.connectors.values())
    return 1 if has_errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
