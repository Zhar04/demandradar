"""Тесты единой модели DemandSignal."""

from demandradar.core.models import Contact, DemandSignal, DemandType, ProductCategory, SignalStatus


def make_signal(**overrides) -> DemandSignal:
    defaults = dict(
        source="goszakup",
        source_id="12345-lot-1",
        demand_type=DemandType.FORMALIZED,
        title="Кровати металлические одноярусные",
        url="https://goszakup.gov.kz/ru/announce/index/12345",
    )
    defaults.update(overrides)
    return DemandSignal(**defaults)


def test_dedup_key_is_stable():
    a = make_signal()
    b = make_signal(title="Другой заголовок")  # тот же source_id -> тот же ключ
    assert a.dedup_key == b.dedup_key


def test_dedup_key_differs_by_source_and_id():
    a = make_signal()
    b = make_signal(source_id="12345-lot-2")
    c = make_signal(source="samruk")
    assert len({a.dedup_key, b.dedup_key, c.dedup_key}) == 3


def test_defaults():
    s = make_signal()
    assert s.status == SignalStatus.NEW
    assert s.category == ProductCategory.OTHER
    assert s.currency == "KZT"
    assert s.collected_at.tzinfo is not None
    assert s.contacts == []


def test_contact_model():
    s = make_signal(contacts=[Contact(name="Иванов И.И.", phone="+7 701 000 00 00", source="goszakup")])
    assert s.contacts[0].name == "Иванов И.И."


def test_serialization_roundtrip():
    s = make_signal()
    data = s.model_dump_json()
    restored = DemandSignal.model_validate_json(data)
    assert restored.dedup_key == s.dedup_key
