"""Коннектор eep.mitwork.kz (ЭТП холдинга «Байтерек»).

Разведка Э0 (2026-07-05): серверный HTML, публичный список
/ru/publics/buys?page=N&per-page=50 (~183k записей), карточка /ru/publics/buy/{id}.
robots.txt: Crawl-delay 10 -> в live-режиме троттлим 10 сек/запрос и ходим
максимум по нескольким страницам за проход (инкрементальный обход).

ВНИМАНИЕ (скрап-контракт): точные CSS-классы живой вёрстки могут отличаться —
парсер держится за СТРУКТУРУ ТАБЛИЦЫ (заголовки колонок), а не за классы.
Первый live-запуск может потребовать подстройки селекторов (ожидаемо, A-14).
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

BASE_URL = "https://eep.mitwork.kz"
LIST_URL = BASE_URL + "/ru/publics/buys"
FIXTURES_DIR = Path(__file__).parent / "fixtures"
MAX_LIVE_PAGES = 3  # инкрементальный проход; backfill глубже — отдельным запуском


def parse_buys_list(html: str) -> list[dict]:
    """Парс таблицы объявлений. Ориентируемся на заголовки колонок, не на классы."""
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

        idx_name = col("наимен")
        idx_num = col("номер", "№")
        idx_sum = col("сумма")
        idx_dates = col("приём", "прием", "срок")
        idx_org = col("организатор", "заказчик")
        idx_status = col("статус")

        for row in table.find_all("tr"):
            cells = row.find_all("td")
            if not cells or idx_name is None or idx_name >= len(cells):
                continue
            link = row.find("a", href=re.compile(r"/publics/buy/\d+"))
            href = link["href"] if link else None
            buy_id = None
            if href:
                match = re.search(r"/publics/buy/(\d+)", href)
                buy_id = match.group(1) if match else None

            def cell(idx: int | None) -> str:
                return clean_text(cells[idx].get_text()) if idx is not None and idx < len(cells) else ""  # noqa: B023

            items.append({
                "id": buy_id or cell(idx_num),
                "number": cell(idx_num),
                "name": cell(idx_name),
                "amount_text": cell(idx_sum),
                "dates_text": cell(idx_dates),
                "organizer": cell(idx_org),
                "status": cell(idx_status),
                "url": absolute(BASE_URL, href),
            })
    return items


def parse_deadline(dates_text: str) -> datetime | None:
    """Из «01.07.2026 09:00 — 17.07.2026 10:00» берём правую (окончание приёма)."""
    found = re.findall(r"\d{2}\.\d{2}\.\d{4}(?:\s+\d{2}:\d{2})?", dates_text or "")
    if not found:
        return None
    return parse_date(found[-1])


@register
class MitworkConnector(Connector):
    key = "mitwork"
    demand_type = DemandType.FORMALIZED
    required_env = ("DR_SCRAPE_LIVE",)  # live-скрапинг включается явным флагом

    def __init__(self, fetcher: Fetcher, mode: ConnectorMode, fixtures_dir: Path | None = None):
        super().__init__(fetcher, mode)
        self.fixtures_dir = fixtures_dir or FIXTURES_DIR

    def fetch(self, since: datetime | None = None) -> Iterable[dict]:
        if self.mode is ConnectorMode.MOCK:
            for path in sorted(self.fixtures_dir.glob("buys_page*.html")):
                yield from parse_buys_list(path.read_text(encoding="utf-8"))
            return
        # live: уважать Crawl-delay 10 из robots.txt
        self.fetcher.config.min_host_interval = max(self.fetcher.config.min_host_interval, 10.0)
        for page in range(1, MAX_LIVE_PAGES + 1):
            response = self.fetcher.get(LIST_URL, params={"page": page, "per-page": 50})
            response.raise_for_status()
            items = parse_buys_list(response.text)
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
            description=f"Способ: {raw.get('status', '')}".strip(),
            customer_name=raw.get("organizer") or None,
            budget=parse_money(raw.get("amount_text")),
            currency="KZT",
            deadline=parse_deadline(raw.get("dates_text", "")),
            url=raw.get("url") or LIST_URL,
            raw=raw,
        )
