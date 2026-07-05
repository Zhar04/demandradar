"""Ядро: один проход конвейера по всем (или выбранным) коннекторам.

  сбор -> нормализация (в коннекторе) -> классификация/фильтр -> дедуп ->
  сохранение -> уведомление -> обновление курсора и health коннектора

Ошибка одного коннектора НЕ роняет проход: фиксируется в connector_state
и в статистике, остальные источники продолжают работать.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime

from demandradar.classify.classifier import Classifier
from demandradar.config import Settings
from demandradar.connectors.base import all_connector_keys, get_connector_class
from demandradar.net.http import Fetcher
from demandradar.notify.telegram import TelegramNotifier
from demandradar.storage.db import Database
from demandradar.storage.repo import ConnectorStateRepository, SignalRepository

logger = logging.getLogger(__name__)


@dataclass
class ConnectorStats:
    mode: str = ""
    collected: int = 0      # нормализованных сигналов от коннектора
    relevant: int = 0       # прошли фильтр категорий
    dropped: int = 0        # отброшены фильтром
    new: int = 0            # новые после дедупа
    duplicates: int = 0     # уже были в БД
    error: str | None = None


@dataclass
class RunReport:
    connectors: dict[str, ConnectorStats] = field(default_factory=dict)

    @property
    def total_new(self) -> int:
        return sum(s.new for s in self.connectors.values())

    def summary_lines(self) -> list[str]:
        lines = []
        for key, s in self.connectors.items():
            status = f"ERROR: {s.error}" if s.error else "ok"
            lines.append(
                f"{key:<12} [{s.mode:<4}] collected={s.collected} relevant={s.relevant} "
                f"dropped={s.dropped} new={s.new} dup={s.duplicates} {status}"
            )
        return lines


def run_once(
    settings: Settings,
    *,
    connector_keys: list[str] | None = None,
    dry_run: bool = False,
    since: datetime | None = None,
    db: Database | None = None,
    fetcher: Fetcher | None = None,
) -> RunReport:
    own_db = db is None
    db = db or Database(settings.db_path)
    db.migrate()
    fetcher = fetcher or Fetcher()

    signals_repo = SignalRepository(db)
    state_repo = ConnectorStateRepository(db)
    classifier = Classifier(settings.categories)
    notifier = TelegramNotifier(
        fetcher,
        bot_token=settings.telegram_bot_token,
        chat_id=settings.telegram_chat_id,
        dry_run=dry_run,
    )

    report = RunReport()
    keys = connector_keys or all_connector_keys()

    for key in keys:
        stats = ConnectorStats()
        report.connectors[key] = stats
        try:
            connector_cls = get_connector_class(key)
            connector = connector_cls.from_settings(fetcher, settings)
            stats.mode = connector.mode.value
            logger.info("[%s] start (mode=%s)", key, connector.mode.value)

            effective_since = since or _cursor_to_datetime(state_repo.get_cursor(key))
            max_published: datetime | None = None

            for signal in connector.collect(since=effective_since):
                stats.collected += 1
                match = classifier.classify(
                    f"{signal.title}\n{signal.description}", signal.matched_codes
                )
                if not match.matched:
                    stats.dropped += 1
                    continue
                stats.relevant += 1
                signal.category = match.category
                signal.matched_keywords = match.keywords
                if match.codes:
                    signal.matched_codes = match.codes

                if signals_repo.save_if_new(signal):
                    stats.new += 1
                    notifier.send_signal(signal)
                else:
                    stats.duplicates += 1

                if signal.published_at and (max_published is None or signal.published_at > max_published):
                    max_published = signal.published_at

            state_repo.record_run(
                key,
                success=True,
                cursor=max_published.isoformat() if max_published else None,
                error=None,
                collected=stats.new,
            )
            logger.info(
                "[%s] done: collected=%d relevant=%d new=%d dup=%d dropped=%d",
                key, stats.collected, stats.relevant, stats.new, stats.duplicates, stats.dropped,
            )
        except Exception as exc:  # noqa: BLE001 — изоляция сбоя источника
            stats.error = f"{type(exc).__name__}: {exc}"
            logger.exception("[%s] connector failed", key)
            state_repo.record_run(key, success=False, error=stats.error, collected=stats.new)

    if own_db:
        db.close()
    return report


def _cursor_to_datetime(cursor: str | None) -> datetime | None:
    if not cursor:
        return None
    try:
        return datetime.fromisoformat(cursor)
    except ValueError:
        return None
