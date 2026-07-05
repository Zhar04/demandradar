"""Коннектор goszakup: MOCK на фикстурах / LIVE по GOSZAKUP_TOKEN.

Единица сигнала — ЛОТ (не объявление): у лота своя сумма, свой код ЕНС ТРУ и
свой заказчик; одно объявление даёт несколько сигналов.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from demandradar.connectors.base import Connector, ConnectorMode, register
from demandradar.connectors.goszakup.client import GoszakupClient
from demandradar.core.models import Contact, DemandSignal, DemandType
from demandradar.net.http import Fetcher

logger = logging.getLogger(__name__)

FIXTURES_DIR = Path(__file__).parent / "fixtures"

# Регионы по первым двум цифрам КАТО (справочно; при неизвестном коде оставляем код)
KATO_REGIONS = {
    "10": "Область Абай",
    "11": "Акмолинская область",
    "15": "Актюбинская область",
    "19": "Алматинская область",
    "23": "Атырауская область",
    "27": "Западно-Казахстанская область",
    "31": "Жамбылская область",
    "33": "Область Жетісу",
    "35": "Карагандинская область",
    "39": "Костанайская область",
    "43": "Кызылординская область",
    "47": "Мангистауская область",
    "55": "Павлодарская область",
    "59": "Северо-Казахстанская область",
    "61": "Туркестанская область",
    "62": "Область Ұлытау",
    "63": "Восточно-Казахстанская область",
    "71": "г. Астана",
    "75": "г. Алматы",
    "79": "г. Шымкент",
}


def kato_to_region(kato_codes: list[str] | None) -> str | None:
    if not kato_codes:
        return None
    code = str(kato_codes[0])
    return KATO_REGIONS.get(code[:2], code)


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


@register
class GoszakupConnector(Connector):
    key = "goszakup"
    demand_type = DemandType.FORMALIZED
    required_env = ("GOSZAKUP_TOKEN",)

    def __init__(self, fetcher: Fetcher, mode: ConnectorMode, token: str = "",
                 fixtures_dir: Path | None = None):
        super().__init__(fetcher, mode)
        self.token = token
        self.fixtures_dir = fixtures_dir or FIXTURES_DIR

    @classmethod
    def from_settings(cls, fetcher: Fetcher, settings):
        token = settings.goszakup_token
        mode = ConnectorMode.LIVE if token else ConnectorMode.MOCK
        return cls(fetcher, mode, token=token)

    # -- fetch ---------------------------------------------------------------

    def fetch(self, since: datetime | None = None) -> Iterable[dict]:
        """Отдаёт сырые элементы «объявление + вложенные лоты» (dict TrdBuy)."""
        if self.mode is ConnectorMode.MOCK:
            yield from self._fetch_mock()
            return
        yield from self._fetch_live(since)

    def _fetch_mock(self) -> Iterable[dict]:
        for path in sorted(self.fixtures_dir.glob("trd_buy_page*.json")):
            payload = json.loads(path.read_text(encoding="utf-8"))
            yield from payload.get("data", {}).get("TrdBuy") or []

    def _fetch_live(self, since: datetime | None) -> Iterable[dict]:
        since = since or datetime.now(UTC) - timedelta(days=1)
        client = GoszakupClient(self.fetcher, self.token)
        for payload in client.iter_trd_buy_pages(publish_date_from=since.strftime("%Y-%m-%d")):
            yield from payload.get("data", {}).get("TrdBuy") or []

    # -- normalize -----------------------------------------------------------

    def normalize(self, raw: dict) -> DemandSignal | None:
        """Не используется напрямую: TrdBuy разворачивается в сигналы per-lot."""
        signals = list(self.normalize_announcement(raw))
        return signals[0] if signals else None

    def collect(self, since: datetime | None = None):
        for raw in self.fetch(since=since):
            try:
                yield from self.normalize_announcement(raw)
            except Exception:  # noqa: BLE001 — одно битое объявление не роняет сбор
                logger.exception("[goszakup] failed to normalize announcement %.120r", raw)

    def normalize_announcement(self, trd_buy: dict) -> Iterable[DemandSignal]:
        anno_id = trd_buy.get("id")
        anno_number = trd_buy.get("numberAnno") or str(anno_id)
        url = f"https://goszakup.gov.kz/ru/announce/index/{anno_id}"
        published_at = _parse_dt(trd_buy.get("publishDate"))
        deadline = _parse_dt(trd_buy.get("endDate"))
        anno_region = kato_to_region(trd_buy.get("kato"))

        lots = trd_buy.get("Lots") or []
        if not lots:
            # объявление без лотов — сигнал по самому объявлению
            yield self._build_signal(
                source_id=f"{anno_number}",
                title=trd_buy.get("nameRu") or f"Объявление {anno_number}",
                description="",
                budget=trd_buy.get("totalSum"),
                quantity=None,
                enstru=[],
                customer_name=trd_buy.get("customerNameRu"),
                customer_bin=trd_buy.get("customerBin"),
                contacts=[],
                region=anno_region,
                url=url,
                published_at=published_at,
                deadline=deadline,
                raw=trd_buy,
            )
            return

        for lot in lots:
            customer = lot.get("Customer") or {}
            contacts = []
            if customer.get("phone") or customer.get("email"):
                contacts.append(
                    Contact(
                        name=customer.get("fullNameRu") or customer.get("nameRu"),
                        role="заказчик (профиль goszakup)",
                        phone=customer.get("phone"),
                        email=customer.get("email"),
                        source="goszakup",
                    )
                )
            lot_region = kato_to_region(lot.get("plnPointKatoList")) or anno_region
            title = lot.get("nameRu") or trd_buy.get("nameRu") or f"Лот {lot.get('lotNumber')}"
            description = lot.get("descriptionRu") or ""

            yield self._build_signal(
                source_id=f"{anno_number}-{lot.get('id')}",
                title=title,
                description=description,
                budget=lot.get("amount"),
                quantity=lot.get("count"),
                enstru=[str(code) for code in (lot.get("enstruList") or [])],
                customer_name=customer.get("nameRu") or lot.get("customerNameRu") or trd_buy.get("customerNameRu"),
                customer_bin=customer.get("bin") or lot.get("customerBin") or trd_buy.get("customerBin"),
                contacts=contacts,
                region=lot_region,
                url=url,
                published_at=published_at,
                deadline=deadline,
                raw={"trd_buy_id": anno_id, "lot": lot},
            )

    def _build_signal(self, *, source_id, title, description, budget, quantity, enstru,
                      customer_name, customer_bin, contacts, region, url,
                      published_at, deadline, raw) -> DemandSignal:
        return DemandSignal(
            source=self.key,
            source_id=source_id,
            demand_type=self.demand_type,
            title=title,
            description=description,
            matched_codes=enstru,  # классификатор сверит с tru_prefixes
            customer_name=customer_name,
            customer_bin=customer_bin,
            quantity=quantity,
            budget=budget,
            currency="KZT",
            region=region,
            deadline=deadline,
            url=url,
            contacts=contacts,
            published_at=published_at,
            raw=raw,
        )
