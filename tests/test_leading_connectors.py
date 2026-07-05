"""Тесты Э5: опережающие коннекторы (data_egov, news_rss, hh) + Модуль B."""

import pytest
import yaml

import demandradar.connectors  # noqa: F401
from demandradar.config import CONFIG_DIR, Settings
from demandradar.connectors.base import ConnectorMode
from demandradar.connectors.data_egov.connector import DataEgovConnector
from demandradar.connectors.hh.connector import HhConnector
from demandradar.connectors.news_rss.connector import NewsRssConnector
from demandradar.core.models import DemandType, ProductCategory
from demandradar.core.pipeline import run_once
from demandradar.storage.db import Database
from demandradar.storage.repo import SignalRepository
from demandradar.watchlist.engine import build_profiles


def load_yaml(name: str) -> dict:
    with open(CONFIG_DIR / name, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


@pytest.fixture(scope="module")
def categories() -> dict:
    return load_yaml("categories.yaml")


@pytest.fixture(scope="module")
def watchlist() -> dict:
    return load_yaml("watchlist.yaml")


# -- data_egov ---------------------------------------------------------------

def test_data_egov_filters_by_oked_and_preclassifies(categories):
    connector = DataEgovConnector(
        None, ConnectorMode.MOCK, oked_watch=categories["oked_watch"]
    )
    signals = {s.customer_bin: s for s in connector.collect()}
    # электромонтаж (43.21) отброшен, 3 интересных ОКЭД прошли
    assert len(signals) == 3
    hostel = signals["260640012345"]
    assert hostel.demand_type == DemandType.LEADING
    assert hostel.pre_classified is True
    assert hostel.category == ProductCategory.BEDS
    assert "Grand Hostel Aktau" in hostel.title
    assert hostel.contacts[0].name == "Сериков Азамат Болатович"
    assert signals["260640023456"].category == ProductCategory.TURNKEY   # аптека
    assert signals["260740034567"].category == ProductCategory.RACKS     # склад


# -- news_rss -----------------------------------------------------------------

def test_news_rss_triggers_and_context_category():
    connector = NewsRssConnector(None, ConnectorMode.MOCK)
    signals = list(connector.collect())
    titles = {s.title for s in signals}
    # 3 новости-триггера прошли, новость про базовую ставку — нет
    assert len(signals) == 3
    assert not any("ставк" in t.lower() for t in titles)

    by_url = {s.url: s for s in signals}
    dc = by_url["https://kapital.kz/business/12345/magnum-otkroet-raspredelitelnyy-centr.html"]
    assert dc.category == ProductCategory.RACKS          # распределительный центр -> стеллажи
    assert dc.pre_classified and dc.demand_type == DemandType.LEADING
    hotel = by_url["https://kapital.kz/business/12388/v-aktau-postroyat-gostinicu-rixos.html"]
    assert hotel.category == ProductCategory.BEDS        # гостиница -> кровати
    trc = by_url["https://kapital.kz/business/12410/bazis-a-sdal-trc-astana.html"]
    assert trc.category == ProductCategory.RETAIL_EQUIPMENT
    assert all(s.published_at is not None for s in signals)


# -- hh -------------------------------------------------------------------------

def test_hh_watchlist_and_role_filter(watchlist):
    connector = HhConnector(None, ConnectorMode.MOCK, watchlist=watchlist)
    signals = list(connector.collect())
    # Magnum продавец + кладовщик прошли; бухгалтер и не-watchlist компании — нет
    assert len(signals) == 2
    assert all("Magnum" in s.title for s in signals)
    assert all(s.pre_classified for s in signals)
    assert {s.region for s in signals} == {"Шымкент"}
    assert signals[0].category == ProductCategory.RACKS  # interest[0] у Magnum


# -- пайплайн: 8 коннекторов, 3 типа спроса -----------------------------------

@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        db_path=tmp_path / "leading.db",
        categories=load_yaml("categories.yaml"),
        scoring=load_yaml("scoring.yaml"),
        watchlist=load_yaml("watchlist.yaml"),
    )


def test_full_pipeline_with_leading_sources(settings):
    keys = ["goszakup", "mitwork", "med_ecc", "fms_ecc", "mpkz",
            "data_egov", "news_rss", "hh"]
    with Database(settings.db_path) as db:
        report = run_once(settings, connector_keys=keys, dry_run=True, db=db)
        for key in keys:
            assert report.connectors[key].error is None, key
            assert report.connectors[key].new > 0, key

        repo = SignalRepository(db)
        demand_types = {b["value"] for b in repo.breakdown("demand_type")}
        assert demand_types == {"formalized", "leading"}

        # опережающие сигналы прошли БЕЗ товарных слов (pre_classified)
        assert report.connectors["data_egov"].new == 3
        assert report.connectors["news_rss"].new == 3
        assert report.connectors["hh"].new == 2

        # Модуль B: карточка Magnum агрегирует новость (РЦ) и вакансии
        cards = {c.name: c for c in build_profiles(repo, settings.watchlist)}
        magnum = cards["Magnum"]
        assert len(magnum.signals) == 3          # 1 новость + 2 вакансии
        assert set(magnum.sources) == {"news_rss", "hh"}
        assert "наём" in magnum.phase or "стройка" in magnum.phase
        # Nazarbayev University цепляется через тендер mp.kz
        nu = cards["Nazarbayev University"]
        assert any(s.source == "mpkz" for s in nu.signals)


def test_watchlist_dashboard_page(settings):
    from fastapi.testclient import TestClient

    from demandradar.dashboard.app import create_app

    run_once(settings, dry_run=True)  # все зарегистрированные коннекторы
    client = TestClient(create_app(settings))
    response = client.get("/watchlist")
    assert response.status_code == 200
    assert "Magnum" in response.text
    assert "Предполагаемая потребность" in response.text
