"""Коннекторы med.ecc.kz и fms.ecc.kz (СК-Фармация / мед. закупки).

Один движок на оба домена (разведка Э0): серверный HTML, поиск /ru/searchanno
(пагинация ?page=N), карточка /ru/announce/index/{id}. robots.txt отсутствует.
Скрап-контракт как у mitwork: парсер держится за заголовки таблицы (A-14).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path

from bs4 import BeautifulSoup

from demandradar.connectors._html import absolute, clean_text, parse_date, parse_money
from demandradar.connectors.base import Connector, ConnectorMode, register
from demandradar.core.models import DemandSignal, DemandType
from demandradar.net.http import Fetcher

logger = logging.getLogger(__name__)

FIXTURES_DIR = Path(__file__).parent / "fixtures"
MAX_LIVE_PAGES = 3


def parse_searchanno(html: str, base_url: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    items: list[dict] = []
    for table in soup.find_all("table"):
        headers = [clean_text(th.get_text()).lower() for th in table.find_all("th")]
        if not headers or not any("наимен" in h for h in headers):
            continue

        def col(*needles: str) -> int | None:
            for i, h in enumerate(headers):  # noqa: B023
                if any(n in h for n in needles):
                    return i
            return None

        idx = {
            "number": col("номер", "№"),
            "name": col("наимен"),
            "org": col("организатор", "заказчик"),
            "region": col("регион"),
            "amount": col("сумма"),
            "status": col("статус"),
            "end": col("окончани", "срок"),
        }

        for row in table.find_all("tr"):
            cells = row.find_all("td")
            if not cells or idx["name"] is None or idx["name"] >= len(cells):
                continue
            link = row.find("a", href=re.compile(r"/announce/index/\d+"))
            href = link["href"] if link else None
            anno_id = None
            if href:
                match = re.search(r"/announce/index/(\d+)", href)
                anno_id = match.group(1) if match else None

            def cell(key: str) -> str:
                i = idx[key]  # noqa: B023
                return clean_text(cells[i].get_text()) if i is not None and i < len(cells) else ""  # noqa: B023

            items.append({
                "id": anno_id or cell("number"),
                "number": cell("number"),
                "name": cell("name"),
                "organizer": cell("org"),
                "region": cell("region"),
                "amount_text": cell("amount"),
                "status": cell("status"),
                "end_text": cell("end"),
                "url": absolute(base_url, href),
            })
    return items


class EccBaseConnector(Connector):
    """Общая логика med/fms.ecc.kz."""

    demand_type = DemandType.FORMALIZED
    required_env = ("DR_SCRAPE_LIVE",)
    base_url = ""
    fixture_glob = ""

    def __init__(self, fetcher: Fetcher, mode: ConnectorMode, fixtures_dir: Path | None = None):
        super().__init__(fetcher, mode)
        self.fixtures_dir = fixtures_dir or FIXTURES_DIR

    def fetch(self, since: datetime | None = None) -> Iterable[dict]:
        if self.mode is ConnectorMode.MOCK:
            for path in sorted(self.fixtures_dir.glob(self.fixture_glob)):
                yield from parse_searchanno(path.read_text(encoding="utf-8"), self.base_url)
            return
        for page in range(1, MAX_LIVE_PAGES + 1):
            response = self.fetcher.get(f"{self.base_url}/ru/searchanno", params={"page": page})
            response.raise_for_status()
            items = parse_searchanno(response.text, self.base_url)
            if not items:
                break
            yield from items

    def normalize(self, raw: dict) -> DemandSignal | None:
        if not raw.get("id") or not raw.get("name"):
            return None
        return DemandSignal(
            source=self.key,
            source_id=str(raw["id"]),
            demand_type=self.demand_type,
            title=raw["name"],
            description=f"Статус: {raw.get('status', '')}".strip(),
            customer_name=raw.get("organizer") or None,
            region=raw.get("region") or None,
            budget=parse_money(raw.get("amount_text")),
            currency="KZT",
            deadline=parse_date(raw.get("end_text")),
            url=raw.get("url") or self.base_url,
            raw=raw,
        )


@register
class MedEccConnector(EccBaseConnector):
    key = "med_ecc"
    base_url = "https://med.ecc.kz"
    fixture_glob = "med_searchanno*.html"


@register
class FmsEccConnector(EccBaseConnector):
    key = "fms_ecc"
    base_url = "https://fms.ecc.kz"
    fixture_glob = "fms_searchanno*.html"
