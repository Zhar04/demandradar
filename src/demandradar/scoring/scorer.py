"""Детерминированный скоринг приоритета сигнала (0..100).

Формула и все веса — в config/scoring.yaml. Без ИИ.
"""

from __future__ import annotations

from datetime import UTC, datetime

from demandradar.core.models import DemandSignal

DEFAULTS: dict = {
    "weights": {"budget": 35, "freshness": 25, "region": 20, "category": 20},
    "budget_buckets": [[2000000, 0.4], [15000000, 0.8], [999999999999, 1.0]],
    "budget_unknown_factor": 0.3,
    "freshness_half_life_days": 7,
    "freshness_unknown_factor": 0.5,
    "home_regions": [],
    "region_factors": {"home": 1.0, "other": 0.6, "unknown": 0.4},
    "category_factors": {},
    "contact_bonus": 5,
}


class Scorer:
    def __init__(self, config: dict | None = None):
        merged = dict(DEFAULTS)
        merged.update(config or {})
        self.config = merged

    # -- факторы 0..1 --------------------------------------------------------

    def budget_factor(self, budget: float | None) -> float:
        if not budget or budget <= 0:
            return float(self.config["budget_unknown_factor"])
        for threshold, factor in self.config["budget_buckets"]:
            if budget <= threshold:
                return float(factor)
        return 1.0

    def freshness_factor(self, published_at: datetime | None, now: datetime | None = None) -> float:
        if published_at is None:
            return float(self.config["freshness_unknown_factor"])
        now = now or datetime.now(UTC)
        if published_at.tzinfo is None:
            published_at = published_at.replace(tzinfo=UTC)
        age_days = max(0.0, (now - published_at).total_seconds() / 86400)
        half_life = float(self.config["freshness_half_life_days"])
        return 0.5 ** (age_days / half_life)

    def region_factor(self, region: str | None) -> float:
        factors = self.config["region_factors"]
        if not region:
            return float(factors.get("unknown", 0.4))
        if region in self.config["home_regions"]:
            return float(factors.get("home", 1.0))
        return float(factors.get("other", 0.6))

    def category_factor(self, category: str) -> float:
        return float(self.config["category_factors"].get(category, 0.5))

    # -- итог ------------------------------------------------------------------

    def score(self, signal: DemandSignal, now: datetime | None = None) -> float:
        weights = self.config["weights"]
        value = (
            weights["budget"] * self.budget_factor(signal.budget)
            + weights["freshness"] * self.freshness_factor(signal.published_at, now)
            + weights["region"] * self.region_factor(signal.region)
            + weights["category"] * self.category_factor(signal.category.value)
        )
        if any(c.phone or c.email for c in signal.contacts):
            value += float(self.config["contact_bonus"])
        return round(min(100.0, max(0.0, value)), 1)
