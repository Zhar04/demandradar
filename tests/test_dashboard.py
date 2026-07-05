"""Тесты дашборда: страницы, смена статуса, отчёт CSV, health, дайджест."""

import pytest
import yaml
from fastapi.testclient import TestClient

import demandradar.connectors  # noqa: F401
from demandradar.config import CONFIG_DIR, Settings
from demandradar.core.pipeline import run_once
from demandradar.dashboard.app import create_app
from demandradar.notify.digest import build_digest
from demandradar.storage.db import Database
from demandradar.storage.repo import SignalRepository


@pytest.fixture
def settings(tmp_path) -> Settings:
    with open(CONFIG_DIR / "categories.yaml", encoding="utf-8") as fh:
        categories = yaml.safe_load(fh)
    with open(CONFIG_DIR / "scoring.yaml", encoding="utf-8") as fh:
        scoring = yaml.safe_load(fh)
    return Settings(db_path=tmp_path / "dash.db", categories=categories, scoring=scoring)


@pytest.fixture
def client(settings) -> TestClient:
    # наполняем БД mock-прогоном (файл на диске: дашборд открывает свои соединения)
    run_once(settings, connector_keys=["goszakup"], dry_run=True)
    return TestClient(create_app(settings))


def test_index_shows_funnel_and_breakdowns(client):
    response = client.get("/")
    assert response.status_code == 200
    html = response.text
    assert "Воронка" in html
    assert "Сигналов собрано" in html
    assert "goszakup" in html
    assert "Кровати" in html          # разбивка по категориям
    assert "Топ по приоритету" in html


def test_signals_list_and_filters(client):
    response = client.get("/signals")
    assert response.status_code == 200
    assert "Кровать медицинская функциональная" in response.text

    filtered = client.get("/signals", params={"category": "beds"})
    assert "Кровать медицинская функциональная" in filtered.text
    assert "Витрина музейная" not in filtered.text

    searched = client.get("/signals", params={"q": "музей"})
    assert "Витрина музейная" in searched.text
    assert "Кровать медицинская" not in searched.text


def test_status_change_persists(client, settings):
    with Database(settings.db_path) as db:
        db.migrate()
        signal = SignalRepository(db).list_filtered(category="beds")[0]

    response = client.post(
        f"/signals/{signal.dedup_key}/status",
        data={"status": "in_work", "back": "/signals"},
        follow_redirects=False,
    )
    assert response.status_code == 303

    with Database(settings.db_path) as db:
        assert SignalRepository(db).get(signal.dedup_key).status.value == "in_work"


def test_connectors_monitoring(client):
    response = client.get("/connectors")
    assert response.status_code == 200
    assert "goszakup" in response.text
    assert "жив" in response.text  # успешный запуск был только что


def test_report_page_and_csv(client):
    page = client.get("/report")
    assert page.status_code == 200
    assert "активных заявок" in page.text

    csv_response = client.get("/report.csv")
    assert csv_response.status_code == 200
    assert "text/csv" in csv_response.headers["content-type"]
    body = csv_response.content.decode("utf-8")
    assert "заголовок" in body.splitlines()[0].lower()
    assert "Кровать медицинская функциональная" in body
    assert "Ахметова" in body  # контакт ЛПР попал в выгрузку


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["signals"] == 8
    assert "goszakup" in data["connectors"]


def test_digest_contains_top_and_counts(settings):
    run_once(settings, connector_keys=["goszakup"], dry_run=True)
    with Database(settings.db_path) as db:
        db.migrate()
        text = build_digest(SignalRepository(db))
    assert "дайджест" in text
    assert "Новых сигналов: <b>8</b>" in text
    assert "Топ по приоритету" in text
    assert "goszakup.gov.kz" in text  # ссылки на первоисточник
