"""Тесты скоринга: факторы, монотонность, бонус за контакт, потолок 100."""

from datetime import UTC, datetime, timedelta

import pytest
import yaml

from demandradar.config import CONFIG_DIR
from demandradar.core.models import Contact, DemandSignal, DemandType, ProductCategory
from demandradar.scoring.scorer import Scorer


@pytest.fixture(scope="module")
def scorer() -> Scorer:
    with open(CONFIG_DIR / "scoring.yaml", encoding="utf-8") as fh:
        return Scorer(yaml.safe_load(fh))


def make_signal(**overrides) -> DemandSignal:
    defaults = dict(
        source="goszakup",
        source_id="s-1",
        demand_type=DemandType.FORMALIZED,
        title="Кровати",
        url="https://example.kz/1",
        category=ProductCategory.BEDS,
        budget=10_000_000.0,
        region="г. Астана",
        published_at=datetime.now(UTC),
    )
    defaults.update(overrides)
    return DemandSignal(**defaults)


def test_budget_monotonic(scorer):
    assert scorer.budget_factor(100_000) < scorer.budget_factor(3_000_000) < scorer.budget_factor(60_000_000)
    assert scorer.budget_factor(None) == scorer.config["budget_unknown_factor"]


def test_freshness_decays(scorer):
    now = datetime.now(UTC)
    fresh = scorer.freshness_factor(now, now)
    week_old = scorer.freshness_factor(now - timedelta(days=7), now)
    month_old = scorer.freshness_factor(now - timedelta(days=30), now)
    assert fresh == pytest.approx(1.0)
    assert week_old == pytest.approx(0.5, abs=0.01)
    assert month_old < week_old


def test_region_factors(scorer):
    assert scorer.region_factor("г. Астана") == 1.0
    assert scorer.region_factor("Атырауская область") < 1.0
    assert scorer.region_factor(None) < scorer.region_factor("Атырауская область")


def test_contact_bonus(scorer):
    base = make_signal()
    with_contact = make_signal(contacts=[Contact(phone="+7 700 000 00 00")])
    assert scorer.score(with_contact) == scorer.score(base) + scorer.config["contact_bonus"]


def test_score_bounds_and_ranking(scorer):
    hot = make_signal(
        budget=60_000_000.0,
        contacts=[Contact(phone="+7", email="a@b.kz")],
    )
    cold = make_signal(
        budget=100_000.0,
        region=None,
        category=ProductCategory.OTHER,
        published_at=datetime.now(UTC) - timedelta(days=45),
    )
    assert 0 <= scorer.score(cold) < scorer.score(hot) <= 100


def test_naive_published_at_ok(scorer):
    # published_at из goszakup — наивный datetime; скоринг не должен падать
    signal = make_signal(published_at=datetime(2026, 7, 3, 9, 15))
    assert scorer.score(signal) > 0
