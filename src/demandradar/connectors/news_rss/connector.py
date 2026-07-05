"""Коннектор RSS деловых новостей КЗ — события-триггеры (ОПЕРЕЖАЮЩИЙ спрос).

Рабочие ленты проверены на Э0 (2026-07-05): kapital.kz/feed (именно /feed),
kz.kursiv.media/feed/, inbusiness.kz/ru/rss, time.kz/rss, profit.kz/rss/.
RSS — легальная машиночитаемая выгрузка; парсим stdlib'ом (xml.etree).

Триггер: новость про открытие/строительство/инвестпроект -> скоро возникнет
спрос на оснащение. Категория угадывается по контексту (гостиница -> кровати,
ТРЦ/магазин -> торговое оборудование, склад -> стеллажи), иначе other.
"""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from collections.abc import Iterable
from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import Path

from demandradar.connectors._html import clean_text
from demandradar.connectors.base import Connector, ConnectorMode, register
from demandradar.core.models import DemandSignal, DemandType, ProductCategory
from demandradar.net.http import Fetcher

logger = logging.getLogger(__name__)

FIXTURES_DIR = Path(__file__).parent / "fixtures"

FEEDS = [
    "https://kapital.kz/feed",
    "https://kz.kursiv.media/feed/",
    "https://inbusiness.kz/ru/rss",
    "https://time.kz/rss",
    "https://profit.kz/rss/",
]

# Стемы-триггеры события (регистронезависимо, по началу слова)
TRIGGER_STEMS = [
    "открыти", "открыва", "откроет", "построит", "строительств", "инвестпроект",
    "инвестици", "новый завод", "новую фабрик", "новый магазин", "новая гостиниц",
    "построят гостиниц", "распределительн", "логистическ", "гипермаркет",
    "супермаркет", "трц", "торгово-развлекательн", "ввод в эксплуатаци",
    "ввёл в эксплуатаци", "сдал в эксплуатаци", "сдан в эксплуатаци",
]

# Контекст -> предполагаемая товарная категория
CONTEXT_CATEGORY = [
    (("гостиниц", "хостел", "отел", "общежити", "санатори"), ProductCategory.BEDS),
    (("больниц", "клиник", "медцентр", "госпитал"), ProductCategory.BEDS),
    (("школ", "детсад", "интернат", "кампус", "университет"), ProductCategory.BEDS),
    # склад специфичнее ретейла: «ретейлер открывает РЦ» — это стеллажи, не ценники
    (("склад", "распределительн", "логистическ", "фулфилмент"), ProductCategory.RACKS),
    (("трц", "торгово-развлекательн", "магазин", "супермаркет", "гипермаркет", "ретейл", "ритейл"),
     ProductCategory.RETAIL_EQUIPMENT),
    (("аптек",), ProductCategory.TURNKEY),
    (("музе",), ProductCategory.SHOWCASES),
    (("офис", "бизнес-центр", "колл-центр"), ProductCategory.OFFICE_CHAIRS),
]


def _stem_hits(text_lower: str, stems: Iterable[str]) -> list[str]:
    return [s for s in stems if re.search(r"\b" + re.escape(s), text_lower)]


def parse_feed(xml_text: str) -> list[dict]:
    """RSS 2.0 / Atom -> список {title, link, description, published}."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        logger.warning("RSS parse error: %r", exc)
        return []
    items = []
    # RSS 2.0
    for item in root.iter("item"):
        items.append({
            "title": clean_text(item.findtext("title") or ""),
            "link": (item.findtext("link") or "").strip(),
            "description": clean_text(re.sub(r"<[^>]+>", " ", item.findtext("description") or "")),
            "published": (item.findtext("pubDate") or "").strip(),
        })
    # Atom
    ns = "{http://www.w3.org/2005/Atom}"
    for entry in root.iter(f"{ns}entry"):
        link_el = entry.find(f"{ns}link")
        items.append({
            "title": clean_text(entry.findtext(f"{ns}title") or ""),
            "link": link_el.get("href") if link_el is not None else "",
            "description": clean_text(re.sub(r"<[^>]+>", " ", entry.findtext(f"{ns}summary") or "")),
            "published": (entry.findtext(f"{ns}updated") or "").strip(),
        })
    return items


def parse_published(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return parsedate_to_datetime(value)
    except (TypeError, ValueError):
        pass
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


@register
class NewsRssConnector(Connector):
    key = "news_rss"
    demand_type = DemandType.LEADING
    required_env = ("DR_SCRAPE_LIVE",)  # сетевые вызовы к лентам — по общему флагу

    def __init__(self, fetcher: Fetcher, mode: ConnectorMode, fixtures_dir: Path | None = None,
                 feeds: list[str] | None = None):
        super().__init__(fetcher, mode)
        self.fixtures_dir = fixtures_dir or FIXTURES_DIR
        self.feeds = feeds or FEEDS

    def fetch(self, since: datetime | None = None) -> Iterable[dict]:
        if self.mode is ConnectorMode.MOCK:
            for path in sorted(self.fixtures_dir.glob("*.xml")):
                yield from parse_feed(path.read_text(encoding="utf-8"))
            return
        for feed_url in self.feeds:
            try:
                response = self.fetcher.get(feed_url)
                response.raise_for_status()
                yield from parse_feed(response.text)
            except Exception as exc:  # noqa: BLE001 — одна лента не роняет остальные
                logger.warning("RSS feed %s failed: %r", feed_url, exc)

    def normalize(self, raw: dict) -> DemandSignal | None:
        title = raw.get("title") or ""
        description = raw.get("description") or ""
        link = raw.get("link") or ""
        if not title or not link:
            return None
        text_lower = f"{title} {description}".lower()

        triggers = _stem_hits(text_lower, TRIGGER_STEMS)
        if not triggers:
            return None  # обычная новость, не событие-триггер

        category = ProductCategory.OTHER
        for context_stems, ctx_category in CONTEXT_CATEGORY:
            if _stem_hits(text_lower, context_stems):
                category = ctx_category
                break

        return DemandSignal(
            source=self.key,
            source_id=link,
            demand_type=self.demand_type,
            title=f"Новость-триггер: {title}",
            description=description[:500],
            category=category,
            matched_keywords=triggers[:5],
            pre_classified=True,
            url=link,
            published_at=parse_published(raw.get("published", "")),
            raw=raw,
        )
