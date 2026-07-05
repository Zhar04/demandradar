"""Тесты скрап-коннекторов Э4 (mitwork, med/fms.ecc, mp.kz) и мультиконнектора."""

import pytest
import yaml

import demandradar.connectors  # noqa: F401 — регистрация
from demandradar.config import CONFIG_DIR, Settings
from demandradar.connectors._html import parse_date, parse_money
from demandradar.connectors.base import ConnectorMode, get_connector_class
from demandradar.connectors.ecc.connector import FmsEccConnector, MedEccConnector
from demandradar.connectors.mitwork.connector import MitworkConnector
from demandradar.connectors.mpkz.connector import MpkzConnector
from demandradar.core.pipeline import run_once
from demandradar.storage.db import Database
from demandradar.storage.repo import SignalRepository


def test_parse_money():
    assert parse_money("994 827,60") == 994827.60
    assert parse_money("1 234 567.89") == 1234567.89
    assert parse_money("8 940 000 ₸") == 8940000.0
    assert parse_money("21 600 000,00") == 21600000.0
    assert parse_money("") is None
    assert parse_money("без суммы") is None


def test_parse_date():
    assert parse_date("16.07.2026 18:00").day == 16
    assert parse_date("2026-07-16").month == 7
    assert parse_date("н/д") is None


def test_mitwork_mock_collect():
    connector = MitworkConnector(fetcher=None, mode=ConnectorMode.MOCK)
    signals = {s.source_id: s for s in connector.collect()}
    assert len(signals) == 4
    beds = signals["198140"]
    assert "двухъярусные" in beds.title
    assert beds.budget == 6_480_000.0
    assert beds.customer_name == "ТОО «Baiterek Service»"
    assert beds.deadline is not None and beds.deadline.day == 18
    assert beds.url == "https://eep.mitwork.kz/ru/publics/buy/198140"


def test_ecc_mock_collect():
    med = {s.source_id: s for s in MedEccConnector(None, ConnectorMode.MOCK).collect()}
    assert len(med) == 3
    bed = med["1741618"]
    assert "функциональная" in bed.title
    assert bed.region == "г. Алматы"
    assert bed.budget == 21_600_000.0
    assert bed.url == "https://med.ecc.kz/ru/announce/index/1741618"

    fms = {s.source_id: s for s in FmsEccConnector(None, ConnectorMode.MOCK).collect()}
    assert len(fms) == 2
    assert fms["521344"].region == "г. Астана"


def test_mpkz_mock_collect():
    signals = {s.source_id: s for s in MpkzConnector(None, ConnectorMode.MOCK).collect()}
    assert len(signals) == 3
    racks = signals["263101"]
    assert "паллетные" in racks.title
    assert racks.budget == 15_300_000.0
    assert racks.customer_name == "АО «Народный Банк Казахстана»"
    assert racks.deadline is not None and racks.deadline.day == 22


def test_scrape_connectors_default_to_mock(monkeypatch):
    """Без DR_SCRAPE_LIVE скраперы обязаны быть в mock-режиме (не ходить в сеть)."""
    monkeypatch.delenv("DR_SCRAPE_LIVE", raising=False)
    settings = Settings()
    for key in ("mitwork", "med_ecc", "fms_ecc", "mpkz"):
        connector = get_connector_class(key).from_settings(None, settings)
        assert connector.mode is ConnectorMode.MOCK, key


@pytest.fixture
def settings(tmp_path) -> Settings:
    with open(CONFIG_DIR / "categories.yaml", encoding="utf-8") as fh:
        categories = yaml.safe_load(fh)
    with open(CONFIG_DIR / "scoring.yaml", encoding="utf-8") as fh:
        scoring = yaml.safe_load(fh)
    return Settings(db_path=tmp_path / "multi.db", categories=categories, scoring=scoring)


def test_multiconnector_pipeline_without_core_changes(settings):
    """Ядро не знает об источниках: 5 коннекторов в одном проходе, общий дедуп/скоринг."""
    keys = ["goszakup", "mitwork", "med_ecc", "fms_ecc", "mpkz"]
    with Database(settings.db_path) as db:
        report = run_once(settings, connector_keys=keys, dry_run=True, db=db)

        for key in keys:
            assert report.connectors[key].error is None, key
            assert report.connectors[key].mode == "mock", key
            assert report.connectors[key].new > 0, key

        # фильтр работает на всех: лифты (mitwork), реагенты (med), лекарства (fms),
        # канцелярия (mpkz), уголь и кровля (goszakup) отброшены
        assert report.connectors["mitwork"].dropped == 1
        assert report.connectors["med_ecc"].dropped == 1
        assert report.connectors["fms_ecc"].dropped == 1
        assert report.connectors["mpkz"].dropped == 1
        assert report.connectors["goszakup"].dropped == 2

        repo = SignalRepository(db)
        assert repo.count() == report.total_new
        sources = {b["value"] for b in repo.breakdown("source")}
        assert sources == set(keys)

        # повторный проход: полный дедуп по всем источникам
        report2 = run_once(settings, connector_keys=keys, dry_run=True, db=db)
        assert report2.total_new == 0
        assert all(s.duplicates > 0 for s in report2.connectors.values())
