"""МОДУЛЬ B: агрегация разнородных сигналов в профили watchlist-компаний.

Карточка: игрок -> сигналы (тендеры/новости/вакансии/регистрации) ->
предполагаемая потребность (interest-категории) -> контакты из сигналов.
Профиль строится по уже собранным сигналам в БД (никакого своего сбора):
watchlist — это ЛИНЗА поверх общего потока, не отдельный конвейер.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from demandradar.core.models import Contact, DemandSignal
from demandradar.storage.repo import SignalRepository

_EPOCH = datetime.min.replace(tzinfo=UTC)


def _stamp(signal: DemandSignal) -> datetime:
    """Момент сигнала, всегда timezone-aware (источники отдают и наивные даты)."""
    stamp = signal.published_at or signal.collected_at
    return stamp if stamp.tzinfo is not None else stamp.replace(tzinfo=UTC)


@dataclass
class CompanyCard:
    name: str
    segment: str
    interest: list[str]
    signals: list[DemandSignal] = field(default_factory=list)
    contacts: list[Contact] = field(default_factory=list)

    @property
    def last_activity(self) -> datetime | None:
        return max((_stamp(s) for s in self.signals), default=None)

    @property
    def sources(self) -> list[str]:
        return sorted({s.source for s in self.signals})

    @property
    def phase(self) -> str:
        """Грубая фаза по составу сигналов (эвристика, без ИИ)."""
        sources = set(self.sources)
        types = {s.demand_type.value for s in self.signals}
        if "formalized" in types:
            return "закупка объявлена — действовать немедленно"
        if "hh" in sources and "news_rss" in sources:
            return "стройка + наём: оснащение вот-вот (2-8 недель)"
        if "hh" in sources:
            return "массовый наём: открытие близко (1-2 месяца)"
        if "news_rss" in sources:
            return "объект анонсирован/строится (3-12 месяцев)"
        return "фоновая активность"


def build_profiles(repo: SignalRepository, watchlist_config: dict) -> list[CompanyCard]:
    cards: list[CompanyCard] = []
    for company in watchlist_config.get("companies", []):
        card = CompanyCard(
            name=company["name"],
            segment=company.get("segment", ""),
            interest=company.get("interest", []),
        )
        seen: set[str] = set()
        for alias in company.get("aliases", []):
            for signal in repo.list_filtered(search=alias, order="date", limit=100):
                if signal.dedup_key in seen:
                    continue
                seen.add(signal.dedup_key)
                card.signals.append(signal)
        card.signals.sort(key=_stamp, reverse=True)

        contact_seen: set[tuple] = set()
        for signal in card.signals:
            for contact in signal.contacts:
                key = (contact.name, contact.phone, contact.email)
                if any(key) and key not in contact_seen:
                    contact_seen.add(key)
                    card.contacts.append(contact)
        cards.append(card)

    # активные — первыми, по свежести
    cards.sort(key=lambda c: (bool(c.signals), c.last_activity or _EPOCH), reverse=True)
    return cards
