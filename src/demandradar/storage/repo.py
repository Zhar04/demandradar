"""Репозитории: сигналы (с дедупом) и состояние коннекторов (курсоры + health)."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from demandradar.core.models import Contact, DemandSignal, SignalStatus
from demandradar.storage.db import Database


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


class SignalRepository:
    def __init__(self, db: Database):
        self.db = db

    def save_if_new(self, signal: DemandSignal) -> bool:
        """INSERT OR IGNORE по dedup_key. True = новый сигнал, False = дубликат."""
        with self.db.conn:
            cursor = self.db.conn.execute(
                """
                INSERT OR IGNORE INTO signals (
                    dedup_key, source, source_id, demand_type, title, description,
                    category, matched_keywords, matched_codes, customer_name, customer_bin,
                    quantity, unit, budget, currency, region, city, deadline, url,
                    contacts, published_at, collected_at, status, score, raw
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    signal.dedup_key,
                    signal.source,
                    signal.source_id,
                    signal.demand_type.value,
                    signal.title,
                    signal.description,
                    signal.category.value,
                    json.dumps(signal.matched_keywords, ensure_ascii=False),
                    json.dumps(signal.matched_codes, ensure_ascii=False),
                    signal.customer_name,
                    signal.customer_bin,
                    signal.quantity,
                    signal.unit,
                    signal.budget,
                    signal.currency,
                    signal.region,
                    signal.city,
                    _iso(signal.deadline),
                    signal.url,
                    json.dumps([c.model_dump() for c in signal.contacts], ensure_ascii=False),
                    _iso(signal.published_at),
                    _iso(signal.collected_at),
                    signal.status.value,
                    signal.score,
                    json.dumps(signal.raw, ensure_ascii=False, default=str),
                ),
            )
        return cursor.rowcount > 0

    def exists(self, dedup_key: str) -> bool:
        row = self.db.conn.execute("SELECT 1 FROM signals WHERE dedup_key=?", (dedup_key,)).fetchone()
        return row is not None

    def count(self) -> int:
        return self.db.conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0]

    def set_status(self, dedup_key: str, status: SignalStatus) -> None:
        with self.db.conn:
            self.db.conn.execute("UPDATE signals SET status=? WHERE dedup_key=?", (status.value, dedup_key))

    def list_recent(self, limit: int = 50) -> list[DemandSignal]:
        rows = self.db.conn.execute(
            "SELECT * FROM signals ORDER BY collected_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [self._row_to_signal(r) for r in rows]

    @staticmethod
    def _row_to_signal(row) -> DemandSignal:
        return DemandSignal(
            source=row["source"],
            source_id=row["source_id"],
            demand_type=row["demand_type"],
            title=row["title"],
            description=row["description"],
            category=row["category"],
            matched_keywords=json.loads(row["matched_keywords"]),
            matched_codes=json.loads(row["matched_codes"]),
            customer_name=row["customer_name"],
            customer_bin=row["customer_bin"],
            quantity=row["quantity"],
            unit=row["unit"],
            budget=row["budget"],
            currency=row["currency"],
            region=row["region"],
            city=row["city"],
            deadline=row["deadline"],
            url=row["url"],
            contacts=[Contact(**c) for c in json.loads(row["contacts"])],
            published_at=row["published_at"],
            collected_at=row["collected_at"],
            status=row["status"],
            score=row["score"],
            raw=json.loads(row["raw"]),
        )


class CompanyCacheRepository:
    """Кеш профилей компаний (обогащение по БИН). TTL пока не нужен: реестры
    статичны, инвалидация — DELETE при необходимости."""

    def __init__(self, db: Database):
        self.db = db

    def get(self, bin_code: str):
        from demandradar.enrich.base import CompanyProfile  # локально: избегаем цикла импортов

        row = self.db.conn.execute("SELECT * FROM company_cache WHERE bin=?", (bin_code,)).fetchone()
        if row is None:
            return None
        return CompanyProfile(
            bin=row["bin"],
            name=row["name"],
            director=row["director"],
            phone=row["phone"],
            email=row["email"],
            address=row["address"],
            oked=row["oked"],
            registered_at=row["registered_at"],
            source=row["source"],
        )

    def put(self, profile) -> None:
        with self.db.conn:
            self.db.conn.execute(
                """
                INSERT OR REPLACE INTO company_cache
                    (bin, name, director, phone, email, address, oked, registered_at, source, fetched_at)
                VALUES (?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    profile.bin,
                    profile.name,
                    profile.director,
                    profile.phone,
                    profile.email,
                    profile.address,
                    profile.oked,
                    _iso(profile.registered_at),
                    profile.source,
                    datetime.now(UTC).isoformat(),
                ),
            )


class ConnectorStateRepository:
    def __init__(self, db: Database):
        self.db = db

    def get_cursor(self, connector_key: str) -> str | None:
        row = self.db.conn.execute(
            "SELECT last_cursor FROM connector_state WHERE connector_key=?", (connector_key,)
        ).fetchone()
        return row["last_cursor"] if row else None

    def record_run(
        self,
        connector_key: str,
        *,
        success: bool,
        cursor: str | None = None,
        error: str | None = None,
        collected: int = 0,
    ) -> None:
        now = datetime.now(UTC).isoformat()
        with self.db.conn:
            self.db.conn.execute(
                """
                INSERT INTO connector_state (connector_key, last_run_at, last_success_at,
                                             last_cursor, last_error, total_collected)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(connector_key) DO UPDATE SET
                    last_run_at = excluded.last_run_at,
                    last_success_at = CASE WHEN ? THEN excluded.last_run_at
                                           ELSE connector_state.last_success_at END,
                    last_cursor = COALESCE(excluded.last_cursor, connector_state.last_cursor),
                    last_error = ?,
                    total_collected = connector_state.total_collected + ?
                """,
                (
                    connector_key,
                    now,
                    now if success else None,
                    cursor,
                    error,
                    collected,
                    success,
                    error,
                    collected,
                ),
            )

    def get_state(self, connector_key: str) -> dict | None:
        row = self.db.conn.execute(
            "SELECT * FROM connector_state WHERE connector_key=?", (connector_key,)
        ).fetchone()
        return dict(row) if row else None

    def all_states(self) -> list[dict]:
        rows = self.db.conn.execute("SELECT * FROM connector_state ORDER BY connector_key").fetchall()
        return [dict(r) for r in rows]
