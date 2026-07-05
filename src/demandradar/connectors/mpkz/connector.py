"""Коннектор mp.kz (крупнейшая частная B2B-ЭТП Казахстана).

Разведка Э0: серверный HTML, /tenders/?category_id=15 — категория «Мебель,
складское оборудование, спортивный инвентарь»; лот /tender/{id}-slug.
robots.txt: Crawl-delay 10 -> уважать в live. Список — карточки со ссылками
на лот; сумма и дедлайн присутствуют в карточке списка.
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

BASE_URL = "https://mp.kz"
LIST_URL = BASE_URL + "/tenders/"
FURNITURE_CATEGORY_ID = 15
FIXTURES_DIR = Path(__file__).parent / "fixtures"
MAX_LIVE_PAGES = 3

TENDER_HREF_RE = re.compile(r"/tender/(\d+)")


def parse_tenders_list(html: str) -> list[dict]:
    """Каждая ссылка на /tender/{id} = лот; поля ищем в ближайшем контейнере."""
    soup = BeautifulSoup(html, "html.parser")
    items: list[dict] = []
    seen: set[str] = set()
    for link in soup.find_all("a", href=TENDER_HREF_RE):
        match = TENDER_HREF_RE.search(link["href"])
        tender_id = match.group(1)
        if tender_id in seen:
            continue
        seen.add(tender_id)

        container = link
        for _ in range(4):  # поднимаемся до карточки лота
            if container.parent is None:
                break
            container = container.parent
            text = container.get_text(" ", strip=True)
            if "₸" in text or "тг" in text.lower() or "заказчик" in text.lower():
                break

        block_text = container.get_text("\n", strip=True)
        amount = None
        amount_match = re.search(r"([\d\s ]+(?:[.,]\d{2})?)\s*(?:₸|тг)", block_text, re.IGNORECASE)
        if amount_match:
            amount = parse_money(amount_match.group(1))
        deadline = None
        deadline_match = re.search(r"до\s+(\d{2}\.\d{2}\.\d{4}(?:\s+\d{2}:\d{2})?)", block_text, re.IGNORECASE)
        if deadline_match:
            deadline = deadline_match.group(1)
        customer = None
        customer_match = re.search(r"Заказчик:\s*(.+)", block_text)
        if customer_match:
            customer = clean_text(customer_match.group(1).splitlines()[0])

        items.append({
            "id": tender_id,
            "name": clean_text(link.get_text()),
            "amount": amount,
            "deadline_text": deadline,
            "customer": customer,
            "url": absolute(BASE_URL, link["href"]),
        })
    return items


@register
class MpkzConnector(Connector):
    key = "mpkz"
    demand_type = DemandType.FORMALIZED
    required_env = ("DR_SCRAPE_LIVE",)

    def __init__(self, fetcher: Fetcher, mode: ConnectorMode, fixtures_dir: Path | None = None):
        super().__init__(fetcher, mode)
        self.fixtures_dir = fixtures_dir or FIXTURES_DIR

    def fetch(self, since: datetime | None = None) -> Iterable[dict]:
        if self.mode is ConnectorMode.MOCK:
            for path in sorted(self.fixtures_dir.glob("tenders_page*.html")):
                yield from parse_tenders_list(path.read_text(encoding="utf-8"))
            return
        # live: Crawl-delay 10 из robots.txt
        self.fetcher.config.min_host_interval = max(self.fetcher.config.min_host_interval, 10.0)
        for page in range(1, MAX_LIVE_PAGES + 1):
            response = self.fetcher.get(
                LIST_URL, params={"category_id": FURNITURE_CATEGORY_ID, "sort": 2, "page": page}
            )
            response.raise_for_status()
            items = parse_tenders_list(response.text)
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
            customer_name=raw.get("customer"),
            budget=raw.get("amount"),
            currency="KZT",
            deadline=parse_date(raw.get("deadline_text")),
            url=raw.get("url") or LIST_URL,
            raw=raw,
        )
