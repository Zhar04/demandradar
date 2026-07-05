"""Коннектор api.hh.ru (hh.kz) — вакансии watchlist-компаний (МОДУЛЬ B).

Разведка Э0: анонимный доступ к /vacancies отдаёт 403 — нужен app token
(регистрация приложения на dev.hh.ru, бесплатно). host=hh.kz, area=40 (РК).
Сигнал: watchlist-компания массово нанимает в городе -> скоро открытие
объекта -> спрос на оснащение. Юр. рамка: данные используем только как
внутренний аналитический сигнал (условия API hh).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path

from demandradar.connectors.base import Connector, ConnectorMode, register
from demandradar.core.models import DemandSignal, DemandType, ProductCategory
from demandradar.net.http import Fetcher

logger = logging.getLogger(__name__)

API_URL = "https://api.hh.ru/vacancies"
FIXTURES_DIR = Path(__file__).parent / "fixtures"
KAZAKHSTAN_AREA = 40

# Роли, сигналящие про открытие/расширение объекта (стемы, lowercase)
EXPANSION_ROLE_STEMS = [
    "продавец", "кассир", "мерчендайзер", "мерчандайзер", "администратор магазин",
    "директор магазин", "управляющий магазин", "кладовщик", "комплектовщик",
    "заведующий склад", "администратор гостиниц", "горничн", "администратор отел",
    "фармацевт", "провизор",
]


@register
class HhConnector(Connector):
    key = "hh"
    demand_type = DemandType.LEADING
    required_env = ("HH_TOKEN",)

    def __init__(self, fetcher: Fetcher, mode: ConnectorMode, token: str = "",
                 watchlist: dict | None = None, fixtures_dir: Path | None = None):
        super().__init__(fetcher, mode)
        self.token = token
        self.watchlist = watchlist or {}
        self.fixtures_dir = fixtures_dir or FIXTURES_DIR

    @classmethod
    def from_settings(cls, fetcher: Fetcher, settings):
        token = settings.hh_token
        mode = ConnectorMode.LIVE if token else ConnectorMode.MOCK
        return cls(fetcher, mode, token=token, watchlist=settings.watchlist)

    def _companies(self) -> list[dict]:
        return self.watchlist.get("companies", [])

    def fetch(self, since: datetime | None = None) -> Iterable[dict]:
        if self.mode is ConnectorMode.MOCK:
            for path in sorted(self.fixtures_dir.glob("vacancies_*.json")):
                payload = json.loads(path.read_text(encoding="utf-8"))
                yield from payload.get("items", [])
            return
        headers = {"Authorization": f"Bearer {self.token}", "HH-User-Agent": "DemandRadar/0.1"}
        for company in self._companies():
            params = {
                "text": company["name"],
                "search_field": "company_name",
                "area": KAZAKHSTAN_AREA,
                "host": "hh.kz",
                "per_page": 50,
            }
            try:
                payload = self.fetcher.get_json(API_URL, params=params, headers=headers)
                yield from payload.get("items", [])
            except Exception as exc:  # noqa: BLE001 — одна компания не роняет обход
                logger.warning("hh search for %s failed: %r", company["name"], exc)

    def _match_company(self, employer_name: str) -> dict | None:
        lowered = employer_name.lower()
        for company in self._companies():
            if any(alias.lower() in lowered for alias in company.get("aliases", [])):
                return company
        return None

    def normalize(self, raw: dict) -> DemandSignal | None:
        employer = (raw.get("employer") or {}).get("name") or ""
        company = self._match_company(employer)
        if company is None:
            return None  # не watchlist-компания
        vacancy_name = raw.get("name") or ""
        role_lower = vacancy_name.lower()
        if not any(stem in role_lower for stem in EXPANSION_ROLE_STEMS):
            return None  # не «расширенческая» роль (бухгалтера не сигналят открытие)

        area = (raw.get("area") or {}).get("name")
        published_at = None
        if raw.get("published_at"):
            try:
                published_at = datetime.fromisoformat(raw["published_at"])
            except ValueError:
                published_at = None

        interest = company.get("interest", ["other"])
        category = ProductCategory(interest[0]) if interest else ProductCategory.OTHER
        return DemandSignal(
            source=self.key,
            source_id=str(raw.get("id")),
            demand_type=self.demand_type,
            title=f"Watchlist-наём: {company['name']} ищет «{vacancy_name}»" + (f" — {area}" if area else ""),
            description=f"Массовый наём watchlist-компании — признак скорого открытия объекта. Работодатель: {employer}.",
            category=category,
            matched_keywords=[f"watchlist:{company['name']}"],
            pre_classified=True,
            customer_name=employer,
            region=area,
            url=raw.get("alternate_url") or "https://hh.kz",
            published_at=published_at,
            raw=raw,
        )
