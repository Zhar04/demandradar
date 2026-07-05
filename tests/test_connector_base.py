"""Тесты интерфейса Connector: реестр, изоляция ошибок в collect()."""

import pytest

from demandradar.connectors.base import Connector, ConnectorMode, get_connector_class, register
from demandradar.core.models import DemandSignal, DemandType


class DummyConnector(Connector):
    key = "dummy_test"
    demand_type = DemandType.FORMALIZED

    def fetch(self, since=None):
        return [
            {"id": "1", "title": "Кровати"},
            {"id": "broken"},          # normalize бросит исключение
            {"id": "3", "title": "Матрасы"},
            {"id": "4", "skip": True}, # normalize вернёт None (не наш лот)
        ]

    def normalize(self, raw):
        if raw.get("skip"):
            return None
        return DemandSignal(
            source=self.key,
            source_id=raw["id"],
            demand_type=self.demand_type,
            title=raw["title"],  # KeyError на "broken"
            url=f"https://example.kz/{raw['id']}",
        )


def test_register_and_lookup():
    register(DummyConnector)
    assert get_connector_class("dummy_test") is DummyConnector


def test_duplicate_registration_rejected():
    with pytest.raises(ValueError):
        register(DummyConnector)


def test_unknown_connector_key():
    with pytest.raises(KeyError):
        get_connector_class("no_such_source")


def test_collect_isolates_bad_items():
    connector = DummyConnector(fetcher=None, mode=ConnectorMode.MOCK)
    signals = list(connector.collect())
    # битый элемент пропущен, skip-элемент отфильтрован, 2 валидных дошли
    assert [s.source_id for s in signals] == ["1", "3"]
