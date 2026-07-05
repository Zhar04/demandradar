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
    parser.add_argument("--daemon", action="store_true", help="режим 24/7: планировщик + дайджест")
    parser.add_argument("--serve", action="store_true", help="запустить веб-дашборд")
    parser.add_argument("--digest", action="store_true", help="отправить дневной дайджест и выйти")
    parser.add_argument("--port", type=int, default=8080, help="порт дашборда (с --serve)")
    parser.add_argument("--host", default="127.0.0.1", help="хост дашборда (в Docker: 0.0.0.0)")
    parser.add_argument("--dry-run", action="store_true", help="не отправлять уведомления (печать в консоль)")
    parser.add_argument("--connector", help="запустить только указанный коннектор")
    parser.add_argument("--backfill", type=int, metavar="N", help="собрать за последние N дней (игнорируя курсор)")
    parser.add_argument("--db", type=Path, help="переопределить путь к БД")
    # МОДУЛЬ A: точечный разбор объявления недвижимости (по ссылке, не массово)
    parser.add_argument("--add-listing", metavar="URL", help="разобрать объявление недвижимости по ссылке (Модуль A)")
    parser.add_argument("--from-file", type=Path, metavar="HTML", help="с --add-listing: взять HTML из файла (офлайн)")
    # Точечная рассылка: черновик по умолчанию, отправка только с --confirm
    parser.add_argument("--outreach", metavar="DEDUP_KEY", help="сформировать письмо по сигналу")
    parser.add_argument("--to", metavar="EMAIL", help="получатель для --outreach")
    parser.add_argument("--confirm", action="store_true", help="действительно отправить (иначе черновик)")
    args = parser.parse_args(argv)

    settings = load_settings()
    if args.db:
        settings.db_path = args.db
    setup_logging(settings.log_level)

    if args.serve:
        import uvicorn

        from demandradar.dashboard.app import create_app

        uvicorn.run(create_app(settings), host=args.host, port=args.port, log_level="info")
        return 0

    if args.daemon:
        from demandradar.core.daemon import Daemon

        Daemon(settings, dry_run=args.dry_run).run_forever()
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

    if args.add_listing:
        from demandradar.net.http import Fetcher
        from demandradar.realestate.module import RealEstateRepository, parse_listing
        from demandradar.storage.db import Database

        if args.from_file:
            html = args.from_file.read_text(encoding="utf-8")
        else:
            with Fetcher() as fetcher:
                response = fetcher.get(args.add_listing)
                response.raise_for_status()
                html = response.text
        contact = parse_listing(html, args.add_listing)
        with Database(settings.db_path) as db:
            db.migrate()
            contact_id = RealEstateRepository(db).add(contact)
        print(f"Сохранено (id={contact_id}): {contact.object_type or 'объект'} · "
              f"{contact.city or '—'} · {contact.deal_type or '—'} · "
              f"{contact.phone or 'телефон не найден — добавь вручную в дашборде'}")
        return 0

    if args.outreach:
        from demandradar.notify.outreach import OutreachService, build_signal_draft
        from demandradar.storage.db import Database
        from demandradar.storage.repo import SignalRepository

        if not args.to:
            parser.error("--outreach требует --to EMAIL")
        with Database(settings.db_path) as db:
            db.migrate()
            signal = SignalRepository(db).get(args.outreach)
            if signal is None:
                print(f"Сигнал {args.outreach!r} не найден")
                return 1
            subject, body = build_signal_draft(signal)
            service = OutreachService(
                db,
                smtp_host=settings.smtp_host, smtp_port=settings.smtp_port,
                smtp_user=settings.smtp_user, smtp_password=settings.smtp_password,
                smtp_from=settings.smtp_from,
            )
            status = service.send_email(
                to=args.to, subject=subject, body=body,
                signal_key=args.outreach, confirm=args.confirm,
            )
        print(f"--- {subject} ---\n{body}\n--- статус: {status} ---")
        if status == "draft" and args.confirm:
            print("SMTP не настроен (.env SMTP_*) — сохранено черновиком.")
        elif status == "draft":
            print("Черновик залогирован. Для отправки добавь --confirm.")
        return 0

    if not args.once:
        parser.error("укажите режим: --once, --daemon, --serve или --digest")

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
