"""Слой БД: SQLite сейчас, Postgres потом.

Правила:
  * Весь SQL живёт в storage/ — ядро и коннекторы SQL не знают.
  * Версионирование схемы через PRAGMA user_version + список миграций.
  * Одно соединение на процесс; WAL для устойчивости 24/7.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

# Миграции применяются по порядку; user_version = индекс последней применённой + 1.
MIGRATIONS: list[str] = [
    # v1: сигналы + состояние коннекторов
    """
    CREATE TABLE signals (
        dedup_key      TEXT PRIMARY KEY,
        source         TEXT NOT NULL,
        source_id      TEXT NOT NULL,
        demand_type    TEXT NOT NULL,
        title          TEXT NOT NULL,
        description    TEXT NOT NULL DEFAULT '',
        category       TEXT NOT NULL DEFAULT 'other',
        matched_keywords TEXT NOT NULL DEFAULT '[]',   -- JSON list
        matched_codes  TEXT NOT NULL DEFAULT '[]',     -- JSON list
        customer_name  TEXT,
        customer_bin   TEXT,
        quantity       REAL,
        unit           TEXT,
        budget         REAL,
        currency       TEXT NOT NULL DEFAULT 'KZT',
        region         TEXT,
        city           TEXT,
        deadline       TEXT,                           -- ISO 8601
        url            TEXT NOT NULL,
        contacts       TEXT NOT NULL DEFAULT '[]',     -- JSON list[Contact]
        published_at   TEXT,
        collected_at   TEXT NOT NULL,
        status         TEXT NOT NULL DEFAULT 'new',
        score          REAL NOT NULL DEFAULT 0,
        raw            TEXT NOT NULL DEFAULT '{}'      -- JSON исходника
    );
    CREATE INDEX idx_signals_status ON signals(status);
    CREATE INDEX idx_signals_source ON signals(source);
    CREATE INDEX idx_signals_category ON signals(category);
    CREATE INDEX idx_signals_collected ON signals(collected_at);

    CREATE TABLE connector_state (
        connector_key   TEXT PRIMARY KEY,
        last_run_at     TEXT,
        last_success_at TEXT,
        last_cursor     TEXT,
        last_error      TEXT,
        total_collected INTEGER NOT NULL DEFAULT 0
    );
    """,
    # v2: кеш профилей компаний для обогащения контактами ЛПР
    """
    CREATE TABLE company_cache (
        bin           TEXT PRIMARY KEY,
        name          TEXT,
        director      TEXT,
        phone         TEXT,
        email         TEXT,
        address       TEXT,
        oked          TEXT,
        registered_at TEXT,
        source        TEXT NOT NULL,
        fetched_at    TEXT NOT NULL
    );
    """,
]


class Database:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")

    def migrate(self) -> None:
        current = self.conn.execute("PRAGMA user_version").fetchone()[0]
        for version, script in enumerate(MIGRATIONS[current:], start=current + 1):
            with self.conn:
                self.conn.executescript(script)
                self.conn.execute(f"PRAGMA user_version = {version}")

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> Database:
        return self

    def __exit__(self, *exc) -> None:
        self.close()
