"""GraphQL-клиент открытого API goszakup.gov.kz (ows.goszakup.gov.kz).

Факты о API (сняты с официального graphdoc, Этап 0, 2026-07-05):
  * POST https://ows.goszakup.gov.kz/v3/graphql, заголовок Authorization: Bearer <token>;
    без токена — 401 даже на интроспекцию.
  * Query.TrdBuy(filter: TrdBuyFiltersInput, limit: Int<=200, after: Int).
  * Пагинация keyset: ответ содержит extensions.pageInfo.{hasNextPage,lastId};
    lastId передаётся как after следующего запроса.
  * Фильтр дат: publishDate: ["с"] или ["с","по"] (формат YYYY-MM-DD).
  * Фильтр по ЕНС ТРУ есть только у Lots (enstruList) — мы фильтруем на своей
    стороне ключевыми словами, коды используем как усиливающий признак.

Запасные пути (если GraphQL сломается): REST /v3/trd-buy; публичный HTML-поиск
/ru/search/announce (без токена). Реализуем при необходимости — интерфейс клиента
это скрывает.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator

from demandradar.net.http import Fetcher

logger = logging.getLogger(__name__)

GRAPHQL_URL = "https://ows.goszakup.gov.kz/v3/graphql"

# Одним запросом: объявление + лоты + заказчик (без N+1), поля сверены со схемой.
TRD_BUY_QUERY = """
query($limit: Int, $after: Int, $filter: TrdBuyFiltersInput) {
  TrdBuy(limit: $limit, after: $after, filter: $filter) {
    id
    numberAnno
    nameRu
    totalSum
    countLots
    publishDate
    endDate
    refBuyStatusId
    kato
    customerBin
    customerNameRu
    orgBin
    orgNameRu
    RefTradeMethods { id nameRu code }
    RefBuyStatus { id nameRu code }
    Lots {
      id
      lotNumber
      nameRu
      descriptionRu
      amount
      count
      enstruList
      plnPointKatoList
      refLotStatusId
      Customer { pid bin nameRu fullNameRu phone email }
    }
  }
}
"""


class GoszakupClient:
    def __init__(self, fetcher: Fetcher, token: str, page_size: int = 100):
        self.fetcher = fetcher
        self.token = token
        self.page_size = min(page_size, 200)  # лимит API — 200

    def _post(self, query: str, variables: dict) -> dict:
        response = self.fetcher.post(
            GRAPHQL_URL,
            json={"query": query, "variables": variables},
            headers={"Authorization": f"Bearer {self.token}"},
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("errors"):
            raise RuntimeError(f"goszakup GraphQL errors: {payload['errors'][:3]}")
        return payload

    def iter_trd_buy_pages(self, publish_date_from: str, publish_date_to: str | None = None) -> Iterator[dict]:
        """Итерация по страницам ответа TrdBuy (сырые payload'ы страницы)."""
        date_filter = [publish_date_from] if publish_date_to is None else [publish_date_from, publish_date_to]
        after: int | None = None
        while True:
            variables: dict = {
                "limit": self.page_size,
                "filter": {"publishDate": date_filter},
            }
            if after is not None:
                variables["after"] = after
            payload = self._post(TRD_BUY_QUERY, variables)
            yield payload

            page_info = (payload.get("extensions") or {}).get("pageInfo") or {}
            if not page_info.get("hasNextPage"):
                break
            after = page_info.get("lastId")
            if after is None:
                logger.warning("goszakup: hasNextPage=true, но lastId отсутствует — останавливаюсь")
                break
