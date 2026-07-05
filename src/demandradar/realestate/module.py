"""МОДУЛЬ A: коммерческая недвижимость и реферальный канал.

Юр. рамка (бриф + разведка Э0): krisha.kz не даёт API; robots.txt объявления
не запрещает, но мы работаем ТОЛЬКО как точечный ассистент — разбор конкретного
объявления по ссылке, которую даёт пользователь. Никакого массового обхода.

Реферальная связка (данные, не «оборот»): партнёр (владелец/УК помещения)
-> приведённый лид (dedup_key сигнала) -> сделка (сумма фиксируется нашим
счётом) -> начисление = deal_amount * referral_pct / 100.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from enum import StrEnum

from bs4 import BeautifulSoup
from pydantic import BaseModel

from demandradar.connectors._html import clean_text, parse_money
from demandradar.storage.db import Database


class RefStatus(StrEnum):
    CANDIDATE = "candidate"        # найден контакт, ещё не звонили
    CONTACTED = "contacted"        # поговорили, объяснили схему
    AGREED = "agreed"              # согласился приводить арендаторов
    LEAD_DELIVERED = "lead_delivered"  # привёл лид (связан с сигналом)
    DEAL_CLOSED = "deal_closed"    # сделка закрыта нашим счётом
    PAID = "paid"                  # вознаграждение выплачено
    DECLINED = "declined"


REF_STATUS_TITLES = {
    RefStatus.CANDIDATE: "Кандидат",
    RefStatus.CONTACTED: "Контакт",
    RefStatus.AGREED: "Партнёр",
    RefStatus.LEAD_DELIVERED: "Лид приведён",
    RefStatus.DEAL_CLOSED: "Сделка закрыта",
    RefStatus.PAID: "Выплачено",
    RefStatus.DECLINED: "Отказ",
}


class RealEstateContact(BaseModel):
    id: int | None = None
    url: str
    object_type: str | None = None
    deal_type: str | None = None
    area_m2: float | None = None
    price: float | None = None
    city: str | None = None
    district: str | None = None
    owner_name: str | None = None
    phone: str | None = None
    listed_at: datetime | None = None
    added_at: datetime | None = None
    ref_status: RefStatus = RefStatus.CANDIDATE
    lead_signal_key: str | None = None
    deal_amount: float | None = None
    referral_pct: float | None = None
    note: str | None = None

    @property
    def referral_amount(self) -> float | None:
        """Начисление ТОЛЬКО от реально закрытой сделки."""
        if self.deal_amount and self.referral_pct and self.ref_status in (
            RefStatus.DEAL_CLOSED, RefStatus.PAID
        ):
            return round(self.deal_amount * self.referral_pct / 100, 2)
        return None


# -- точечный парсер объявления (krisha и похожие доски) -------------------------

def parse_listing(html: str, url: str) -> RealEstateContact:
    """Извлечь, что доступно в открытой части объявления. Телефон на krisha
    скрыт за JS — если его нет в HTML, поле останется пустым (заполнить руками)."""
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text("\n", strip=True)

    title = clean_text(soup.title.get_text()) if soup.title else ""
    h1 = soup.find("h1")
    if h1:
        title = clean_text(h1.get_text()) or title

    deal_type = None
    lowered = f"{title}\n{text[:2000]}".lower()
    if "аренд" in lowered:
        deal_type = "аренда"
    elif "продаж" in lowered or "продам" in lowered:
        deal_type = "продажа"

    area = None
    area_match = re.search(r"(\d{2,5}(?:[.,]\d{1,2})?)\s*(?:м²|м2|кв\.?\s*м)", lowered)
    if area_match:
        area = float(area_match.group(1).replace(",", "."))

    price = None
    price_match = re.search(r"([\d\s ]{4,})\s*(?:₸|тг|тенге)", text)
    if price_match:
        price = parse_money(price_match.group(1))

    object_type = None
    for candidate in ("магазин", "торговое помещение", "склад", "офис", "помещение свободного назначения", "бутик"):
        if candidate in lowered:
            object_type = candidate
            break

    city = None
    city_match = re.search(r"(Алматы|Астана|Шымкент|Караганда|Актобе|Атырау|Актау|Костанай|Павлодар|Тараз|Усть-Каменогорск|Семей|Кокшетау|Туркестан)", text)
    if city_match:
        city = city_match.group(1)

    owner = None
    owner_match = re.search(r"(?:Хозяин|Владелец|Собственник|Автор объявления)[:\s]+([А-ЯЁA-Z][^\n,]{2,40})", text)
    if owner_match:
        owner = clean_text(owner_match.group(1))

    phone = None
    phone_match = re.search(r"(?:\+7|8)[\s(]*7\d{2}[\s)]*\d{3}[\s-]*\d{2}[\s-]*\d{2}", text)
    if phone_match:
        phone = clean_text(phone_match.group(0))

    return RealEstateContact(
        url=url,
        object_type=object_type,
        deal_type=deal_type,
        area_m2=area,
        price=price,
        city=city,
        owner_name=owner,
        phone=phone,
    )


# -- хранилище --------------------------------------------------------------------

class RealEstateRepository:
    def __init__(self, db: Database):
        self.db = db

    def add(self, contact: RealEstateContact) -> int:
        with self.db.conn:
            cursor = self.db.conn.execute(
                """
                INSERT OR IGNORE INTO real_estate_contacts
                    (url, object_type, deal_type, area_m2, price, city, district,
                     owner_name, phone, listed_at, added_at, ref_status,
                     lead_signal_key, deal_amount, referral_pct, note)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    contact.url, contact.object_type, contact.deal_type, contact.area_m2,
                    contact.price, contact.city, contact.district, contact.owner_name,
                    contact.phone,
                    contact.listed_at.isoformat() if contact.listed_at else None,
                    datetime.now(UTC).isoformat(),
                    contact.ref_status.value, contact.lead_signal_key,
                    contact.deal_amount, contact.referral_pct, contact.note,
                ),
            )
        if cursor.rowcount == 0:
            row = self.db.conn.execute(
                "SELECT id FROM real_estate_contacts WHERE url=?", (contact.url,)
            ).fetchone()
            return row["id"]
        return cursor.lastrowid

    def list_all(self) -> list[RealEstateContact]:
        rows = self.db.conn.execute(
            "SELECT * FROM real_estate_contacts ORDER BY added_at DESC"
        ).fetchall()
        return [self._to_model(r) for r in rows]

    def get(self, contact_id: int) -> RealEstateContact | None:
        row = self.db.conn.execute(
            "SELECT * FROM real_estate_contacts WHERE id=?", (contact_id,)
        ).fetchone()
        return self._to_model(row) if row else None

    def update_referral(self, contact_id: int, *, ref_status: RefStatus | None = None,
                        lead_signal_key: str | None = None, deal_amount: float | None = None,
                        referral_pct: float | None = None, note: str | None = None) -> None:
        sets, params = [], []
        for column, value in (
            ("ref_status", ref_status.value if ref_status else None),
            ("lead_signal_key", lead_signal_key),
            ("deal_amount", deal_amount),
            ("referral_pct", referral_pct),
            ("note", note),
        ):
            if value is not None:
                sets.append(f"{column}=?")
                params.append(value)
        if not sets:
            return
        params.append(contact_id)
        with self.db.conn:
            self.db.conn.execute(
                f"UPDATE real_estate_contacts SET {', '.join(sets)} WHERE id=?", params
            )

    @staticmethod
    def _to_model(row) -> RealEstateContact:
        return RealEstateContact(
            id=row["id"],
            url=row["url"],
            object_type=row["object_type"],
            deal_type=row["deal_type"],
            area_m2=row["area_m2"],
            price=row["price"],
            city=row["city"],
            district=row["district"],
            owner_name=row["owner_name"],
            phone=row["phone"],
            listed_at=row["listed_at"],
            added_at=row["added_at"],
            ref_status=RefStatus(row["ref_status"]),
            lead_signal_key=row["lead_signal_key"],
            deal_amount=row["deal_amount"],
            referral_pct=row["referral_pct"],
            note=row["note"],
        )
