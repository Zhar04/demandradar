"""Обогащение сигнала контактом ЛПР по БИН заказчика.

Провайдеры (сейчас mock, живые по мере ключей):
  * MockRegistryProvider — фикстуры, имитирующие ответ реестра компаний
    (Kompra/Adata/data.egov gbd_ul: руководитель, телефон, e-mail, ОКЭД).
  * Э5-live: DataEgovProvider (датасет gbd_ul, бесплатный apiKey).
  * Опция владельца: платные Kompra/Adata — тот же интерфейс.

Результаты кешируются в БД (company_cache): реестры меняются редко,
дёргать их на каждый сигнал нельзя.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from pydantic import BaseModel


class CompanyProfile(BaseModel):
    bin: str
    name: str | None = None
    director: str | None = None
    phone: str | None = None
    email: str | None = None
    address: str | None = None
    oked: str | None = None
    registered_at: datetime | None = None
    source: str = "mock"


class EnrichmentProvider(Protocol):
    name: str

    def lookup(self, bin_code: str) -> CompanyProfile | None:
        """Профиль компании по БИН или None (не нашли/недоступно). Не бросает."""
        ...
