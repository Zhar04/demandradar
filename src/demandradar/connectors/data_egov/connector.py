"""Коннектор data.egov.kz — новые юрлица по ОКЭД (ОПЕРЕЖАЮЩИЙ спрос).

Разведка Э0: датасет gbd_ul (ГБД «Юридические лица», Минюст) живой, отдаёт
наименование, дату регистрации, БИН, адрес, ОКЭД, ФИО руководителя.
API v4: GET https://data.egov.kz/api/v4/gbd_ul/v1?apiKey=...&source={ES-JSON}.
Без apiKey — 403 (проверено), поэтому live только при DATA_EGOV_APIKEY.

Сигнал: «зарегистрирована компания интересного нам ОКЭД» (гостиница ->
кровати/матрасы через 1-3 месяца). Категория берётся из oked_watch
(config/categories.yaml), сигнал помечается pre_classified.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path

from demandradar.connectors._html import clean_text
from demandradar.connectors.base import Connector, ConnectorMode, register
from demandradar.core.models import Contact, DemandSignal, DemandType, ProductCategory
from demandradar.net.http import Fetcher

logger = logging.getLogger(__name__)

API_URL = "https://data.egov.kz/api/v4/gbd_ul/v1"
DATASET_URL = "https://data.egov.kz/datasets/view?index=gbd_ul"
FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _first(record: dict, *keys: str) -> str | None:
    """Датасеты egov непоследовательны в именах полей — пробуем варианты."""
    for key in keys:
        value = record.get(key)
        if value:
            return str(value).strip()
    return None


@register
class DataEgovConnector(Connector):
    key = "data_egov"
    demand_type = DemandType.LEADING
    required_env = ("DATA_EGOV_APIKEY",)

    def __init__(self, fetcher: Fetcher, mode: ConnectorMode, api_key: str = "",
                 oked_watch: dict | None = None, fixtures_dir: Path | None = None):
        super().__init__(fetcher, mode)
        self.api_key = api_key
        self.oked_watch = oked_watch or {}
        self.fixtures_dir = fixtures_dir or FIXTURES_DIR

    @classmethod
    def from_settings(cls, fetcher: Fetcher, settings):
        api_key = settings.data_egov_apikey
        mode = ConnectorMode.LIVE if api_key else ConnectorMode.MOCK
        return cls(fetcher, mode, api_key=api_key,
                   oked_watch=settings.categories.get("oked_watch", {}))

    def fetch(self, since: datetime | None = None) -> Iterable[dict]:
        if self.mode is ConnectorMode.MOCK:
            for path in sorted(self.fixtures_dir.glob("gbd_ul_page*.json")):
                yield from json.loads(path.read_text(encoding="utf-8"))
            return
        source_query = {"size": 100}
        if since is not None:
            source_query["query"] = {
                "range": {"registerdate": {"gte": since.strftime("%Y-%m-%d")}}
            }
        payload = self.fetcher.get_json(
            API_URL,
            params={"apiKey": self.api_key, "source": json.dumps(source_query, ensure_ascii=False)},
        )
        items = payload if isinstance(payload, list) else payload.get("data", [])
        yield from items

    # -- нормализация -----------------------------------------------------------

    def _match_oked_group(self, oked_code: str | None) -> tuple[str, dict] | None:
        if not oked_code:
            return None
        for group_key, group in self.oked_watch.items():
            for code in group.get("codes", []):
                if oked_code.startswith(code):
                    return group_key, group
        return None

    def normalize(self, raw: dict) -> DemandSignal | None:
        oked_code = _first(raw, "okedcode", "oked", "oked_code")
        matched = self._match_oked_group(oked_code)
        if matched is None:
            return None  # компания вне интересных нам ОКЭД
        group_key, group = matched

        bin_code = _first(raw, "bin", "binCode")
        name = _first(raw, "namerus", "name_ru", "name") or f"БИН {bin_code}"
        director = _first(raw, "director", "directorname", "fio")
        reg_date = _first(raw, "registerdate", "regdate", "registration_date")
        address = _first(raw, "address", "addressrus", "legaladdress")

        published_at = None
        if reg_date:
            try:
                published_at = datetime.fromisoformat(reg_date[:19])
            except ValueError:
                published_at = None

        contacts = []
        if director:
            contacts.append(Contact(name=director, role="руководитель (при регистрации)", source=self.key))

        category = ProductCategory(group.get("category", "other"))
        return DemandSignal(
            source=self.key,
            source_id=bin_code or name,
            demand_type=self.demand_type,
            title=f"Новая компания: {name} — {group.get('title', group_key)}",
            description=clean_text(
                f"ОКЭД {oked_code}. Зарегистрирована {reg_date or '—'}. {address or ''}"
            ),
            category=category,
            matched_keywords=[f"ОКЭД {oked_code}"],
            pre_classified=True,
            customer_name=name,
            customer_bin=bin_code,
            region=None,
            url=DATASET_URL,
            contacts=contacts,
            published_at=published_at,
            raw=raw,
        )
