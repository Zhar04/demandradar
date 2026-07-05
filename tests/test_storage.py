"""Тесты слоя хранения: миграции, дедуп, состояние коннекторов."""

import pytest

from demandradar.core.models import DemandSignal, DemandType, SignalStatus
from demandradar.storage.db import MIGRATIONS, Database
from demandradar.storage.repo import ConnectorStateRepository, SignalRepository


@pytest.fixture
def db():
    database = Database(":memory:")
    database.migrate()
    yield database
    database.close()


def make_signal(source_id="anno-1-lot-1", **overrides) -> DemandSignal:
    defaults = dict(
        source="goszakup",
        source_id=source_id,
        demand_type=DemandType.FORMALIZED,
        title="Кровати металлические",
        url="https://goszakup.gov.kz/ru/announce/index/1",
        budget=1_000_000.0,
        region="г. Астана",
    )
    defaults.update(overrides)
    return DemandSignal(**defaults)


def test_migrate_idempotent(db):
    db.migrate()  # повторный вызов не падает и не дублирует схему
    assert db.conn.execute("PRAGMA user_version").fetchone()[0] == len(MIGRATIONS)


def test_incremental_migration_from_v1():
    """БД, созданная на этапе v1, безопасно докатывается до текущей версии."""
    database = Database(":memory:")
    with database.conn:
        database.conn.executescript(MIGRATIONS[0])
        database.conn.execute("PRAGMA user_version = 1")
    database.migrate()
    assert database.conn.execute("PRAGMA user_version").fetchone()[0] == len(MIGRATIONS)
    # таблица из v2 существует
    assert database.conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='company_cache'"
    ).fetchone() is not None
    database.close()


def test_save_if_new_dedup(db):
    repo = SignalRepository(db)
    assert repo.save_if_new(make_signal()) is True
    assert repo.save_if_new(make_signal()) is False  # тот же source_id
    assert repo.save_if_new(make_signal(source_id="anno-1-lot-2")) is True
    assert repo.count() == 2


def test_roundtrip_signal(db):
    repo = SignalRepository(db)
    original = make_signal()
    repo.save_if_new(original)
    restored = repo.list_recent()[0]
    assert restored.dedup_key == original.dedup_key
    assert restored.title == original.title
    assert restored.budget == original.budget
    assert restored.status == SignalStatus.NEW


def test_set_status(db):
    repo = SignalRepository(db)
    signal = make_signal()
    repo.save_if_new(signal)
    repo.set_status(signal.dedup_key, SignalStatus.IN_WORK)
    assert repo.list_recent()[0].status == SignalStatus.IN_WORK


def test_connector_state_success_and_failure(db):
    repo = ConnectorStateRepository(db)
    assert repo.get_cursor("goszakup") is None

    repo.record_run("goszakup", success=True, cursor="2026-07-04T10:00:00", collected=5)
    state = repo.get_state("goszakup")
    assert state["last_cursor"] == "2026-07-04T10:00:00"
    assert state["last_success_at"] is not None
    assert state["last_error"] is None
    assert state["total_collected"] == 5

    # сбой: курсор НЕ затирается, ошибка фиксируется, last_success_at сохраняется
    repo.record_run("goszakup", success=False, error="FetchError: 503")
    state = repo.get_state("goszakup")
    assert state["last_cursor"] == "2026-07-04T10:00:00"
    assert state["last_error"] == "FetchError: 503"
    assert state["last_success_at"] is not None
    assert state["total_collected"] == 5
