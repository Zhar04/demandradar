"""Интеграционный тест конвейера: mock goszakup -> фильтр -> дедуп -> отчёт."""

import pytest
import yaml

import demandradar.connectors  # noqa: F401 — регистрация коннекторов
from demandradar.config import CONFIG_DIR, Settings
from demandradar.core.pipeline import run_once
from demandradar.storage.db import Database
from demandradar.storage.repo import SignalRepository


@pytest.fixture
def settings(tmp_path) -> Settings:
    with open(CONFIG_DIR / "categories.yaml", encoding="utf-8") as fh:
        categories = yaml.safe_load(fh)
    return Settings(db_path=tmp_path / "test.db", categories=categories)


@pytest.fixture
def db() -> Database:
    database = Database(":memory:")
    yield database
    database.close()


def test_first_run_collects_and_filters(settings, db):
    report = run_once(settings, connector_keys=["goszakup"], dry_run=True, db=db)
    stats = report.connectors["goszakup"]

    assert stats.error is None
    assert stats.mode == "mock"          # без GOSZAKUP_TOKEN — мок-режим
    assert stats.collected == 10
    assert stats.relevant == 8           # уголь и ремонт кровли отброшены
    assert stats.dropped == 2
    assert stats.new == 8
    assert stats.duplicates == 0
    assert report.total_new == 8

    # сигналы реально в БД, категории проставлены
    repo = SignalRepository(db)
    assert repo.count() == 8
    categories = {s.category.value for s in repo.list_recent(limit=20)}
    assert {"beds", "mattresses", "bedding", "office_chairs",
            "furniture_ldsp", "showcases", "kitchen"} <= categories


def test_second_run_dedups_everything(settings, db):
    run_once(settings, connector_keys=["goszakup"], dry_run=True, db=db)
    report = run_once(settings, connector_keys=["goszakup"], dry_run=True, db=db)
    stats = report.connectors["goszakup"]

    assert stats.new == 0
    assert stats.duplicates == 8
    assert SignalRepository(db).count() == 8


def test_connector_state_updated(settings, db):
    from demandradar.storage.repo import ConnectorStateRepository

    run_once(settings, connector_keys=["goszakup"], dry_run=True, db=db)
    state = ConnectorStateRepository(db).get_state("goszakup")
    assert state is not None
    assert state["last_success_at"] is not None
    assert state["last_error"] is None
    # курсор = максимальный publishDate из фикстур
    assert state["last_cursor"].startswith("2026-07-05")
    assert state["total_collected"] == 8


def test_enrichment_and_scoring_applied(settings, db):
    run_once(settings, connector_keys=["goszakup"], dry_run=True, db=db)
    repo = SignalRepository(db)
    signals = {s.source_id: s for s in repo.list_recent(limit=20)}

    bed = signals["12345678-1-55100201"]
    # контакт ЛПР добавлен из mock-реестра по БИН
    directors = [c for c in bed.contacts if c.role == "первый руководитель (реестр)"]
    assert directors and directors[0].name == "Ахметова Сауле Бекеновна"
    # скор посчитан и осмыслен: у всех сигналов > 0
    assert all(s.score > 0 for s in signals.values())
    # горячий лот (14.4 млн, есть контакты) выше холодного без контактов
    kitchen = signals["12347001-1"]
    assert bed.score > kitchen.score


def test_llm_refines_other_category(settings, db):
    """Опциональный LLM уточняет категорию «мутного» сигнала; без LLM — other."""
    from demandradar.connectors.base import Connector, register
    from demandradar.core.models import DemandType
    from demandradar.llm.base import LLMProvider

    class OtherSrcConnector(Connector):
        key = "other_src"
        demand_type = DemandType.FORMALIZED

        def fetch(self, since=None):
            return [{"id": "x1"}]

        def normalize(self, raw):
            from demandradar.core.models import DemandSignal

            return DemandSignal(
                source=self.key,
                source_id=raw["id"],
                demand_type=self.demand_type,
                title="Бельё гостиничное оптом",  # только global-сеть -> other
                url="https://example.kz/x1",
            )

    try:
        register(OtherSrcConnector)
    except ValueError:
        pass

    class FakeLLM(LLMProvider):
        name = "fake"

        def is_available(self):
            return True

        def complete(self, prompt, *, system=None, max_tokens=512):
            return "bedding"

    run_once(settings, connector_keys=["other_src"], dry_run=True, db=db, llm=FakeLLM())
    repo = SignalRepository(db)
    saved = [s for s in repo.list_recent(limit=5) if s.source == "other_src"]
    assert saved and saved[0].category.value == "bedding"


def test_broken_connector_does_not_crash_run(settings, db, monkeypatch):
    """Падение одного источника фиксируется, но не роняет проход."""
    from demandradar.connectors.base import Connector, register
    from demandradar.core.models import DemandType

    class BrokenConnector(Connector):
        key = "broken_src"
        demand_type = DemandType.FORMALIZED

        def fetch(self, since=None):
            raise RuntimeError("source is down")

        def normalize(self, raw):
            return None

    try:
        register(BrokenConnector)
    except ValueError:
        pass  # уже зарегистрирован предыдущим прогоном модуля

    report = run_once(settings, connector_keys=["broken_src", "goszakup"], dry_run=True, db=db)
    assert report.connectors["broken_src"].error is not None
    assert "source is down" in report.connectors["broken_src"].error
    # goszakup при этом отработал
    assert report.connectors["goszakup"].error is None
    assert report.connectors["goszakup"].new == 8
