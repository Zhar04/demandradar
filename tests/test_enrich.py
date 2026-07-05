"""Тесты обогащения контактом ЛПР: провайдеры, кеш, устойчивость."""

import pytest

from demandradar.core.models import Contact, DemandSignal, DemandType
from demandradar.enrich.base import CompanyProfile
from demandradar.enrich.enricher import ContactEnricher
from demandradar.enrich.providers import MockRegistryProvider
from demandradar.storage.db import Database
from demandradar.storage.repo import CompanyCacheRepository


@pytest.fixture
def db():
    database = Database(":memory:")
    database.migrate()
    yield database
    database.close()


def make_signal(**overrides) -> DemandSignal:
    defaults = dict(
        source="goszakup",
        source_id="a-1",
        demand_type=DemandType.FORMALIZED,
        title="Кровати",
        url="https://example.kz/1",
        customer_bin="990240004456",
    )
    defaults.update(overrides)
    return DemandSignal(**defaults)


def test_mock_provider_lookup():
    provider = MockRegistryProvider()
    profile = provider.lookup("990240004456")
    assert profile is not None
    assert profile.director == "Ахметова Сауле Бекеновна"
    assert provider.lookup("000000000000") is None


def test_enrich_adds_director_contact(db):
    enricher = ContactEnricher([MockRegistryProvider()], cache=CompanyCacheRepository(db))
    signal = make_signal()
    assert enricher.enrich(signal) is True
    contact = signal.contacts[-1]
    assert contact.name == "Ахметова Сауле Бекеновна"
    assert contact.role == "первый руководитель (реестр)"
    assert contact.phone == "+7 (7212) 41-20-01"


def test_enrich_without_bin_is_noop(db):
    enricher = ContactEnricher([MockRegistryProvider()], cache=CompanyCacheRepository(db))
    signal = make_signal(customer_bin=None)
    assert enricher.enrich(signal) is False
    assert signal.contacts == []


def test_cache_roundtrip_and_priority(db):
    cache = CompanyCacheRepository(db)
    cache.put(CompanyProfile(bin="111", name="ТОО Тест", director="Директор", source="unit"))

    calls = {"n": 0}

    class CountingProvider:
        name = "counting"

        def lookup(self, bin_code):
            calls["n"] += 1
            return None

    enricher = ContactEnricher([CountingProvider()], cache=cache)
    profile = enricher.profile_for("111")
    assert profile is not None and profile.name == "ТОО Тест"
    assert calls["n"] == 0  # кеш сработал, провайдер не тронут


def test_provider_exception_does_not_propagate(db):
    class ExplodingProvider:
        name = "boom"

        def lookup(self, bin_code):
            raise RuntimeError("registry down")

    enricher = ContactEnricher(
        [ExplodingProvider(), MockRegistryProvider()], cache=CompanyCacheRepository(db)
    )
    signal = make_signal()
    assert enricher.enrich(signal) is True  # второй провайдер спас


def test_duplicate_channel_still_adds_director(db):
    """Телефон уже есть из объявления, но появляется ИМЯ руководителя — добавляем."""
    enricher = ContactEnricher([MockRegistryProvider()], cache=CompanyCacheRepository(db))
    signal = make_signal(
        contacts=[Contact(phone="+7 (7212) 41-20-01", source="goszakup")]
    )
    assert enricher.enrich(signal) is True
    assert signal.contacts[-1].name == "Ахметова Сауле Бекеновна"
