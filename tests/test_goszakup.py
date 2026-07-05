"""Тесты коннектора goszakup: mock-фикстуры, нормализация, GraphQL-пагинация."""

import json

import httpx
import pytest

from demandradar.connectors.base import ConnectorMode
from demandradar.connectors.goszakup.client import GoszakupClient
from demandradar.connectors.goszakup.connector import GoszakupConnector, kato_to_region
from demandradar.core.models import DemandType
from demandradar.net.http import Fetcher, FetcherConfig


@pytest.fixture
def connector() -> GoszakupConnector:
    return GoszakupConnector(fetcher=None, mode=ConnectorMode.MOCK)


def test_mock_collect_yields_signal_per_lot(connector):
    signals = list(connector.collect())
    # 6 объявлений с 9 лотами + 1 объявление без лотов = 10 сигналов
    assert len(signals) == 10
    assert all(s.source == "goszakup" for s in signals)
    assert all(s.demand_type == DemandType.FORMALIZED for s in signals)
    # ключи дедупа уникальны
    assert len({s.dedup_key for s in signals}) == 10


def test_normalization_fields(connector):
    signals = {s.source_id: s for s in connector.collect()}
    bed = signals["12345678-1-55100201"]
    assert bed.title == "Кровать медицинская функциональная трёхсекционная"
    assert bed.budget == 14_400_000.0
    assert bed.quantity == 120.0
    assert bed.region == "Карагандинская область"
    assert bed.customer_bin == "990240004456"
    assert bed.customer_name and "больница" in bed.customer_name.lower()
    assert bed.url == "https://goszakup.gov.kz/ru/announce/index/17290101"
    assert bed.published_at is not None and bed.published_at.year == 2026
    assert bed.deadline is not None
    assert bed.contacts and bed.contacts[0].phone == "+7 (7212) 41-22-33"
    assert bed.matched_codes == ["4571230"]  # ИД ЕНС ТРУ как есть


def test_announcement_without_lots(connector):
    signals = {s.source_id: s for s in connector.collect()}
    kitchen = signals["12347001-1"]
    assert "Кухонный гарнитур" in kitchen.title
    assert kitchen.budget == 1_850_000.0
    assert kitchen.region == "Туркестанская область"


def test_kato_mapping():
    assert kato_to_region(["750000000"]) == "г. Алматы"
    assert kato_to_region(["351010000"]) == "Карагандинская область"
    assert kato_to_region(["999123"]) == "999123"  # неизвестный код не теряем
    assert kato_to_region([]) is None
    assert kato_to_region(None) is None


def test_graphql_client_pagination_and_auth():
    calls: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append({"auth": request.headers.get("Authorization"), "vars": body["variables"]})
        if len(calls) == 1:
            return httpx.Response(200, json={
                "data": {"TrdBuy": [{"id": 1}, {"id": 2}]},
                "extensions": {"pageInfo": {"hasNextPage": True, "lastId": 2}},
            })
        return httpx.Response(200, json={
            "data": {"TrdBuy": [{"id": 3}]},
            "extensions": {"pageInfo": {"hasNextPage": False, "lastId": 3}},
        })

    fetcher = Fetcher(
        config=FetcherConfig(min_host_interval=0.0, backoff_base=0.0),
        transport=httpx.MockTransport(handler),
    )
    client = GoszakupClient(fetcher, token="test-token", page_size=2)
    pages = list(client.iter_trd_buy_pages("2026-07-01", "2026-07-05"))

    assert len(pages) == 2
    assert calls[0]["auth"] == "Bearer test-token"
    assert calls[0]["vars"]["filter"] == {"publishDate": ["2026-07-01", "2026-07-05"]}
    assert "after" not in calls[0]["vars"]
    assert calls[1]["vars"]["after"] == 2  # keyset-пагинация по lastId


def test_graphql_errors_raise():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"errors": [{"message": "Invalid token"}]})

    fetcher = Fetcher(
        config=FetcherConfig(min_host_interval=0.0, backoff_base=0.0),
        transport=httpx.MockTransport(handler),
    )
    client = GoszakupClient(fetcher, token="bad")
    with pytest.raises(RuntimeError, match="GraphQL errors"):
        list(client.iter_trd_buy_pages("2026-07-01"))
