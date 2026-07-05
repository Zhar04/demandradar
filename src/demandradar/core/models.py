"""Единая модель данных: DemandSignal и связанные перечисления.

Все коннекторы, независимо от источника, приводят данные к DemandSignal.
Ядро (дедуп, классификация, скоринг, дашборд) работает только с этой моделью.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class DemandType(StrEnum):
    """Тип спроса (см. бриф)."""

    FORMALIZED = "formalized"  # объявленная закупка (тендер, запрос цен)
    LATENT = "latent"          # ищут, но не публиковали закупку
    LEADING = "leading"        # событие-триггер: спрос вот-вот возникнет


class SignalStatus(StrEnum):
    """Стадии воронки. Должны 1:1 совпадать с дашбордом и docs/PLAYBOOK.md."""

    NEW = "new"                # новый, не разобран
    IN_WORK = "in_work"        # взят в работу
    CONTACTED = "contacted"    # состоялся контакт с ЛПР
    KP_SENT = "kp_sent"        # отправлено КП
    MEETING = "meeting"        # назначена/проведена встреча
    DEAL = "deal"              # сделка
    REJECTED = "rejected"      # отказ / нерелевантно


class ProductCategory(StrEnum):
    """Наши товарные группы (маппинг из config/categories.yaml)."""

    BEDS = "beds"                      # кровати металлические и ЛДСП
    MATTRESSES = "mattresses"          # матрасы, в т.ч. люкс
    BEDDING = "bedding"                # постельное бельё
    FURNITURE_LDSP = "furniture_ldsp"  # ЛДСП-мебель (шкафы, столы, тумбы)
    KITCHEN = "kitchen"                # кухонные гарнитуры
    OFFICE_CHAIRS = "office_chairs"    # офисные кресла
    RACKS = "racks"                    # складские стеллажи
    RETAIL_EQUIPMENT = "retail_equipment"  # ценникодержатели, крючки, торговое оборудование
    SHOWCASES = "showcases"            # витрины: торговые/музейные/ювелирные
    MDF = "mdf"                        # МДФ-панели
    TURNKEY = "turnkey"                # аптеки/ювелирные салоны «под ключ»
    OTHER = "other"                    # прошло фильтр, но группа не определена


class Contact(BaseModel):
    """Контакт (ЛПР или контакт из объявления)."""

    name: str | None = None
    role: str | None = None       # должность/роль: директор, снабжение, контакт из лота
    phone: str | None = None
    email: str | None = None
    source: str | None = None     # откуда контакт: goszakup, statsnet, объявление...


class DemandSignal(BaseModel):
    """Единый сигнал спроса. Один сигнал = одна потенциальная сделка/лид."""

    # Идентификация
    source: str                          # ключ коннектора: goszakup, samruk, ...
    source_id: str                       # уникальный id внутри источника (номер лота/объявления/URL)
    demand_type: DemandType

    # Содержание
    title: str
    description: str = ""
    category: ProductCategory = ProductCategory.OTHER
    matched_keywords: list[str] = Field(default_factory=list)  # чем зацепился фильтр
    matched_codes: list[str] = Field(default_factory=list)     # коды ТРУ/ЕНС, если были
    # Коннектор САМ определил релевантность и категорию (опережающие сигналы:
    # новые компании по ОКЭД, новости-триггеры, вакансии watchlist). Классификатор
    # ядра такие сигналы не перепроверяет — товарных слов в них нет по природе.
    pre_classified: bool = False

    # Заказчик
    customer_name: str | None = None
    customer_bin: str | None = None      # БИН/ИИН/ИНН

    # Объём и деньги
    quantity: float | None = None
    unit: str | None = None
    budget: float | None = None          # сумма в тенге (или валюте источника)
    currency: str = "KZT"

    # География и сроки
    region: str | None = None            # область/регион
    city: str | None = None
    deadline: datetime | None = None     # срок подачи/поставки

    # Связь
    url: str                             # ссылка на первоисточник
    contacts: list[Contact] = Field(default_factory=list)

    # Служебное
    published_at: datetime | None = None
    collected_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    status: SignalStatus = SignalStatus.NEW
    score: float = 0.0                   # приоритет (Этап 2)
    raw: dict = Field(default_factory=dict)  # исходные данные как есть

    @property
    def dedup_key(self) -> str:
        """Стабильный ключ дедупликации: источник + id внутри источника."""
        base = f"{self.source}:{self.source_id}"
        return hashlib.sha256(base.encode("utf-8")).hexdigest()[:32]
