"""Тесты Э6: Wordstat (латентный), Модуль A, очередь обзвона, рассылка."""

from pathlib import Path

import pytest
import yaml

import demandradar.connectors  # noqa: F401
from demandradar.config import CONFIG_DIR, Settings
from demandradar.connectors.base import ConnectorMode
from demandradar.connectors.wordstat.connector import WordstatConnector
from demandradar.core.models import DemandType, ProductCategory
from demandradar.core.pipeline import run_once
from demandradar.notify.outreach import UNSUBSCRIBE_FOOTER, OutreachService, build_signal_draft
from demandradar.realestate.module import (
    RealEstateRepository,
    RefStatus,
    parse_listing,
)
from demandradar.storage.db import Database
from demandradar.storage.repo import SignalRepository

FIXTURES = Path(__file__).parent / "fixtures"


def load_yaml(name: str) -> dict:
    with open(CONFIG_DIR / name, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        db_path=tmp_path / "s6.db",
        categories=load_yaml("categories.yaml"),
        scoring=load_yaml("scoring.yaml"),
        watchlist=load_yaml("watchlist.yaml"),
    )


@pytest.fixture
def db():
    database = Database(":memory:")
    database.migrate()
    yield database
    database.close()


# -- Wordstat -------------------------------------------------------------------

def test_wordstat_growth_threshold_and_volume():
    config = load_yaml("categories.yaml")["wordstat"]
    connector = WordstatConnector(None, ConnectorMode.MOCK, wordstat_config=config)
    signals = list(connector.collect())
    # кровати +65% и стеллажи +38% прошли; витрины +5% (нет роста) и
    # мдф (95 < min_volume 100) — нет
    assert len(signals) == 2
    beds = next(s for s in signals if "кровати оптом" in s.title)
    assert beds.demand_type == DemandType.LATENT
    assert beds.category == ProductCategory.BEDS
    assert beds.pre_classified
    assert "+65%" in beds.title
    racks = next(s for s in signals if "стеллажи" in s.title)
    assert racks.region == "г. Алматы"


def test_latent_signal_reaches_funnel(settings):
    with Database(settings.db_path) as db:
        report = run_once(settings, connector_keys=["wordstat"], dry_run=True, db=db)
        assert report.connectors["wordstat"].new == 2
        types = {b["value"] for b in SignalRepository(db).breakdown("demand_type")}
        assert types == {"latent"}


# -- Модуль A ---------------------------------------------------------------------

def test_parse_listing_extracts_fields():
    html = (FIXTURES / "krisha_listing.html").read_text(encoding="utf-8")
    contact = parse_listing(html, "https://krisha.kz/a/show/12345678")
    assert contact.deal_type == "аренда"
    assert contact.area_m2 == 180.0
    assert contact.price == 1_260_000.0
    assert contact.city == "Алматы"
    assert contact.object_type in ("магазин", "торговое помещение")
    assert contact.owner_name and "Ержан" in contact.owner_name
    assert contact.phone and "701" in contact.phone


def test_realestate_repo_and_referral_accrual(db):
    html = (FIXTURES / "krisha_listing.html").read_text(encoding="utf-8")
    contact = parse_listing(html, "https://krisha.kz/a/show/12345678")
    repo = RealEstateRepository(db)
    contact_id = repo.add(contact)
    assert repo.add(contact) == contact_id  # дедуп по URL

    # партнёр привёл лид, сделка закрыта: начисление = 2 400 000 * 3% = 72 000
    repo.update_referral(contact_id, ref_status=RefStatus.DEAL_CLOSED,
                         lead_signal_key="abc123", deal_amount=2_400_000, referral_pct=3)
    saved = repo.get(contact_id)
    assert saved.ref_status == RefStatus.DEAL_CLOSED
    assert saved.referral_amount == 72_000.0

    # до закрытия сделки начисления нет
    repo.update_referral(contact_id, ref_status=RefStatus.AGREED)
    assert repo.get(contact_id).referral_amount is None


# -- рассылка ---------------------------------------------------------------------

def test_outreach_draft_without_confirm(db, settings):
    run_once(settings, connector_keys=["goszakup"], dry_run=True, db=db)
    signal = SignalRepository(db).list_filtered(category="beds")[0]

    subject, body = build_signal_draft(signal)
    assert signal.title[:40] in subject or signal.title[:40] in body
    assert "отписаться" in body            # обязательная отписка
    assert UNSUBSCRIBE_FOOTER in body

    service = OutreachService(db)  # SMTP не настроен
    status = service.send_email(to="test@example.kz", subject=subject, body=body,
                                signal_key=signal.dedup_key, confirm=False)
    assert status == "draft"
    # даже с confirm без SMTP — только черновик, ничего не отправляется
    assert service.send_email(to="test@example.kz", subject=subject, body=body,
                              signal_key=signal.dedup_key, confirm=True) == "draft"

    history = service.history()
    assert len(history) == 2
    assert all(h["status"] == "draft" for h in history)
    assert history[0]["recipient"] == "test@example.kz"


# -- дашборд: рефералы + очередь обзвона --------------------------------------------

def test_dashboard_referrals_and_callqueue(settings):
    from fastapi.testclient import TestClient

    from demandradar.dashboard.app import create_app

    run_once(settings, connector_keys=["goszakup"], dry_run=True)
    with Database(settings.db_path) as db:
        db.migrate()
        html = (FIXTURES / "krisha_listing.html").read_text(encoding="utf-8")
        contact_id = RealEstateRepository(db).add(
            parse_listing(html, "https://krisha.kz/a/show/999")
        )

    client = TestClient(create_app(settings))
    page = client.get("/referrals")
    assert page.status_code == 200
    assert "Ержан" in page.text

    response = client.post(f"/referrals/{contact_id}/update", data={
        "ref_status": "deal_closed", "lead_signal_key": "k1",
        "deal_amount": "1000000", "referral_pct": "5",
    }, follow_redirects=True)
    assert "50 000" in response.text  # начислено 5% от 1 млн

    queue = client.get("/callqueue.csv")
    assert queue.status_code == 200
    body = queue.content.decode("utf-8")
    assert "Кровать медицинская функциональная" in body
    assert "Ахметова" in body or "41-22-33" in body
