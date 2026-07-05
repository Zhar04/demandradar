"""Демон 24/7: планировщик опроса коннекторов + дневной дайджест.

  * интервал опроса: DR_POLL_MINUTES (дефолт 30) или poll_minutes источника
    в config/sources.yaml;
  * каждый коннектор продолжает с курсора в connector_state (переживает рестарт);
  * сбой коннектора не роняет демона (изоляция уже в run_once);
  * graceful shutdown по Ctrl+C / SIGTERM: дорабатываем текущий цикл и выходим;
  * дайджест раз в день в DR_DIGEST_HOUR (дефолт 17, локальное время).
"""

from __future__ import annotations

import logging
import os
import signal
import threading
from datetime import UTC, date, datetime

from demandradar.config import Settings
from demandradar.connectors.base import all_connector_keys
from demandradar.core.pipeline import run_once
from demandradar.net.http import Fetcher
from demandradar.notify.digest import build_digest
from demandradar.notify.telegram import TelegramNotifier
from demandradar.storage.db import Database
from demandradar.storage.repo import SignalRepository

logger = logging.getLogger(__name__)

TICK_SECONDS = 20  # шаг цикла; сами коннекторы ходят по своим интервалам


def poll_interval_minutes(settings: Settings, connector_key: str) -> int:
    default = int(os.environ.get("DR_POLL_MINUTES", "30") or 30)
    source = (settings.sources.get("sources") or {}).get(connector_key) or {}
    return int(source.get("poll_minutes", default))


class Daemon:
    def __init__(self, settings: Settings, *, dry_run: bool = False):
        self.settings = settings
        self.dry_run = dry_run
        self.stop_event = threading.Event()
        self._last_run: dict[str, datetime] = {}
        self._last_digest_day: date | None = None
        self.digest_hour = int(os.environ.get("DR_DIGEST_HOUR", "17") or 17)

    # -- планирование -----------------------------------------------------------

    def due_connectors(self, now: datetime) -> list[str]:
        due = []
        for key in all_connector_keys():
            interval = poll_interval_minutes(self.settings, key)
            last = self._last_run.get(key)
            if last is None or (now - last).total_seconds() >= interval * 60:
                due.append(key)
        return due

    def digest_due(self, now_local: datetime) -> bool:
        return now_local.hour >= self.digest_hour and self._last_digest_day != now_local.date()

    # -- работа -------------------------------------------------------------------

    def run_forever(self) -> None:
        self._install_signal_handlers()
        logger.info(
            "Daemon started: connectors=%s, default poll=%s min, digest at %s:00",
            ", ".join(all_connector_keys()),
            os.environ.get("DR_POLL_MINUTES", "30"),
            self.digest_hour,
        )
        while not self.stop_event.is_set():
            self.tick()
            self.stop_event.wait(TICK_SECONDS)
        logger.info("Daemon stopped gracefully")

    def tick(self, now: datetime | None = None) -> list[str]:
        """Один шаг цикла. Возвращает список опрошенных коннекторов (для тестов)."""
        now = now or datetime.now(UTC)
        due = self.due_connectors(now)
        for key in due:
            if self.stop_event.is_set():
                break
            try:
                report = run_once(self.settings, connector_keys=[key], dry_run=self.dry_run)
                stats = report.connectors[key]
                if stats.error:
                    logger.warning("[daemon] %s finished with error: %s", key, stats.error)
            except Exception:  # noqa: BLE001 — демон не умирает никогда
                logger.exception("[daemon] unexpected failure in %s", key)
            self._last_run[key] = now

        now_local = datetime.now()
        if self.digest_due(now_local):
            self._send_digest()
            self._last_digest_day = now_local.date()
        return due

    def _send_digest(self) -> None:
        try:
            with Database(self.settings.db_path) as db:
                db.migrate()
                text = build_digest(SignalRepository(db))
            with Fetcher() as fetcher:
                TelegramNotifier(
                    fetcher,
                    bot_token=self.settings.telegram_bot_token,
                    chat_id=self.settings.telegram_chat_id,
                    dry_run=self.dry_run,
                ).send_text(text)
            logger.info("Daily digest dispatched")
        except Exception:  # noqa: BLE001
            logger.exception("Digest failed (will retry tomorrow)")

    # -- shutdown --------------------------------------------------------------------

    def _install_signal_handlers(self) -> None:
        def handler(signum, frame):  # noqa: ARG001
            logger.info("Signal %s received, shutting down after current cycle...", signum)
            self.stop_event.set()

        signal.signal(signal.SIGINT, handler)
        try:
            signal.signal(signal.SIGTERM, handler)
        except (AttributeError, ValueError):
            pass  # SIGTERM недоступен в некоторых окружениях Windows
