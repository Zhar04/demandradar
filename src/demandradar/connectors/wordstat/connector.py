"""Коннектор Яндекс Wordstat — ЛАТЕНТНЫЙ спрос (поиск как датчик).

Разведка Э0: отдельный бесплатный Wordstat API (2024): базовый URL
https://api.wordstat.yandex.net, методы /v1/dynamics, /v1/topRequests,
/v1/regions; OAuth-токен + заявка в поддержку Директа; квота 10 rps /
1000 запросов-сутки; регионы Казахстана поддерживаются.

Логика сигнала: запрос из config/categories.yaml (wordstat.queries) вырос
к прошлому периоду на growth_threshold_pct и объём >= min_volume ->
«в регионе растёт латентный спрос на категорию».
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from demandradar.connectors.base import Connector, ConnectorMode, register
from demandradar.core.models import DemandSignal, DemandType, ProductCategory
from demandradar.net.http import Fetcher

logger = logging.getLogger(__name__)

API_URL = "https://api.wordstat.yandex.net/v1/dynamics"
FIXTURES_DIR = Path(__file__).parent / "fixtures"


@register
class WordstatConnector(Connector):
    key = "wordstat"
    demand_type = DemandType.LATENT
    required_env = ("YANDEX_DIRECT_TOKEN",)

    def __init__(self, fetcher: Fetcher, mode: ConnectorMode, token: str = "",
                 wordstat_config: dict | None = None, fixtures_dir: Path | None = None):
        super().__init__(fetcher, mode)
        self.token = token
        config = wordstat_config or {}
        self.queries: dict[str, str] = config.get("queries", {})
        self.growth_threshold = float(config.get("growth_threshold_pct", 30))
        self.min_volume = int(config.get("min_volume", 100))
        self.fixtures_dir = fixtures_dir or FIXTURES_DIR

    @classmethod
    def from_settings(cls, fetcher: Fetcher, settings):
        token = settings.yandex_direct_token
        mode = ConnectorMode.LIVE if token else ConnectorMode.MOCK
        return cls(fetcher, mode, token=token,
                   wordstat_config=settings.categories.get("wordstat", {}))

    def fetch(self, since: datetime | None = None) -> Iterable[dict]:
        if self.mode is ConnectorMode.MOCK:
            for path in sorted(self.fixtures_dir.glob("dynamics_*.json")):
                yield from json.loads(path.read_text(encoding="utf-8"))
            return
        headers = {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"}
        for query in self.queries:
            try:
                payload = self.fetcher.post_json(
                    API_URL,
                    json={"phrase": query, "period": "monthly"},
                    headers=headers,
                )
                payload["query"] = query
                yield payload
            except Exception as exc:  # noqa: BLE001 — один запрос не роняет остальные
                logger.warning("wordstat dynamics for %r failed: %r", query, exc)

    def normalize(self, raw: dict) -> DemandSignal | None:
        query = raw.get("query") or raw.get("phrase") or ""
        if query not in self.queries:
            return None
        dynamics = raw.get("dynamics") or []
        if len(dynamics) < 2:
            return None
        previous, current = dynamics[-2], dynamics[-1]
        prev_count = float(previous.get("count") or 0)
        curr_count = float(current.get("count") or 0)
        if curr_count < self.min_volume or prev_count <= 0:
            return None
        growth_pct = (curr_count - prev_count) / prev_count * 100
        if growth_pct < self.growth_threshold:
            return None

        region = raw.get("region") or "Казахстан"
        period = str(current.get("period") or "")
        category = ProductCategory(self.queries[query])
        return DemandSignal(
            source=self.key,
            source_id=f"{query}|{region}|{period}",
            demand_type=self.demand_type,
            title=f"Латентный спрос: «{query}» +{growth_pct:.0f}% ({region})",
            description=(
                f"Поисковый интерес вырос с {prev_count:.0f} до {curr_count:.0f} "
                f"показов/мес (период {period}). Ищут, но закупку не публиковали — "
                f"окно для проактивного захода."
            ),
            category=category,
            matched_keywords=[query],
            pre_classified=True,
            quantity=curr_count,
            unit="показов/мес",
            region=region if region != "Казахстан" else None,
            url=f"https://wordstat.yandex.ru/#!/?words={quote(query)}",
            published_at=None,
            raw=raw,
        )
