"""Тесты демона: расписание, изоляция сбоев, дайджест раз в день, shutdown."""

from datetime import UTC, datetime, timedelta

import pytest
import yaml

import demandradar.connectors  # noqa: F401
from demandradar.config import CONFIG_DIR, Settings
from demandradar.connectors.base import all_connector_keys
from demandradar.core.daemon import Daemon, poll_interval_minutes


@pytest.fixture
def settings(tmp_path) -> Settings:
    with open(CONFIG_DIR / "categories.yaml", encoding="utf-8") as fh:
        categories = yaml.safe_load(fh)
    return Settings(db_path=tmp_path / "daemon.db", categories=categories,
                    sources={"sources": {"goszakup": {"poll_minutes": 5}}})


def test_poll_interval_override(settings, monkeypatch):
    monkeypatch.delenv("DR_POLL_MINUTES", raising=False)
    assert poll_interval_minutes(settings, "goszakup") == 5    # из sources.yaml
    assert poll_interval_minutes(settings, "mitwork") == 30    # дефолт
    monkeypatch.setenv("DR_POLL_MINUTES", "10")
    assert poll_interval_minutes(settings, "mitwork") == 10


def test_due_scheduling(settings):
    daemon = Daemon(settings, dry_run=True)
    now = datetime.now(UTC)
    # первый тик: должны быть все
    assert set(daemon.due_connectors(now)) == set(all_connector_keys())
    daemon._last_run = {k: now for k in all_connector_keys()}
    # сразу после запуска — никто не due
    assert daemon.due_connectors(now + timedelta(minutes=1)) == []
    # goszakup (5 мин) станет due раньше остальных (30 мин)
    due_6min = daemon.due_connectors(now + timedelta(minutes=6))
    assert due_6min == ["goszakup"]


def test_tick_runs_due_and_survives_failures(settings, monkeypatch):
    daemon = Daemon(settings, dry_run=True)
    calls = []

    def fake_run_once(s, *, connector_keys, dry_run):
        calls.append(connector_keys[0])
        if connector_keys[0] == "mitwork":
            raise RuntimeError("boom")  # демон обязан пережить
        from demandradar.core.pipeline import ConnectorStats, RunReport
        report = RunReport()
        report.connectors[connector_keys[0]] = ConnectorStats()
        return report

    monkeypatch.setattr("demandradar.core.daemon.run_once", fake_run_once)
    now = datetime.now(UTC)
    daemon.digest_hour = 25  # дайджест не мешает тесту
    processed = daemon.tick(now)
    assert set(processed) == set(all_connector_keys())
    assert set(calls) == set(all_connector_keys())  # mitwork упал, остальные отработали
    # все получили отметку последнего запуска — не будут перезапущены немедленно
    assert daemon.tick(now + timedelta(minutes=1)) == []


def test_digest_once_per_day(settings):
    daemon = Daemon(settings, dry_run=True)
    daemon.digest_hour = 0  # «пора» в любой час
    noon = datetime(2026, 7, 5, 12, 0)
    assert daemon.digest_due(noon) is True
    daemon._last_digest_day = noon.date()
    assert daemon.digest_due(noon) is False                      # уже слали сегодня
    assert daemon.digest_due(noon + timedelta(days=1)) is True   # завтра снова


def test_stop_event_breaks_loop(settings, monkeypatch):
    daemon = Daemon(settings, dry_run=True)
    monkeypatch.setattr(
        "demandradar.core.daemon.run_once",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not run")),
    )
    daemon.stop_event.set()
    daemon.digest_hour = 25
    daemon.tick(datetime.now(UTC))  # run_once не вызывается (иначе AssertionError)
    # run_forever с установленным stop_event выходит сразу (без вечного цикла)
    daemon.run_forever()
