"""Тесты детерминированного классификатора (categories.yaml)."""

import pytest
import yaml

from demandradar.classify.classifier import Classifier
from demandradar.config import CONFIG_DIR
from demandradar.core.models import ProductCategory


@pytest.fixture(scope="module")
def classifier() -> Classifier:
    with open(CONFIG_DIR / "categories.yaml", encoding="utf-8") as fh:
        return Classifier(yaml.safe_load(fh))


@pytest.mark.parametrize(
    ("text", "category"),
    [
        ("Кровать медицинская функциональная трёхсекционная", ProductCategory.BEDS),
        ("Приобретение кроватей двухъярусных для общежития", ProductCategory.BEDS),
        ("Матрац противопролежневый с компрессором", ProductCategory.MATTRESSES),
        ("Комплект постельного белья 1,5-спальный, бязь", ProductCategory.BEDDING),
        ("Подушки и одеяла для интерната", ProductCategory.BEDDING),
        ("Кресло офисное эргономичное для руководителя", ProductCategory.OFFICE_CHAIRS),
        ("Стеллажи паллетные для склада готовой продукции", ProductCategory.RACKS),
        ("Витрина музейная вертикальная со стеклом", ProductCategory.SHOWCASES),
        ("Кухонный гарнитур для пищеблока", ProductCategory.KITCHEN),
        ("Панели МДФ стеновые для отделки актового зала", ProductCategory.MDF),
        ("Аптека под ключ: дизайн-проект и оснащение", ProductCategory.TURNKEY),
    ],
)
def test_positive_categories(classifier, text, category):
    result = classifier.classify(text)
    assert result.matched, text
    assert result.category == category


@pytest.mark.parametrize(
    "text",
    [
        "Уголь каменный марки Д для котельных",
        "Работы по ремонту кровли административного здания",  # «кровля» != «кровать»
        "Услуги по охране объектов",
        "Бензин АИ-92",
    ],
)
def test_irrelevant_not_matched(classifier, text):
    assert classifier.classify(text).matched is False


@pytest.mark.parametrize(
    "text",
    [
        "Кресло-коляска инвалидная с ручным приводом",
        "Шкаф серверный 19 дюймов 42U",
        "Шкаф управления насосной станцией",
        "Витрина холодильная среднетемпературная",
        "Аптечка первой помощи автомобильная",
    ],
)
def test_negative_keywords_veto(classifier, text):
    """Негативные слова отбивают и категорию, и глобальную сеть."""
    assert classifier.classify(text).matched is False


def test_tru_code_match_without_keywords(classifier):
    result = classifier.classify("Изделия мебельные прочие", ["310912.500.000015"])
    assert result.matched
    assert result.codes == ["310912.500.000015"]
    assert result.category in (ProductCategory.BEDS, ProductCategory.FURNITURE_LDSP)


def test_global_net_falls_to_other(classifier):
    # «мебель» без уточнений: категория не определена, но сигнал релевантен
    result = classifier.classify("Услуги по перетяжке мебели")
    assert result.matched
    # «мебел» есть и в категории furniture_ldsp -> она и победит
    assert result.category == ProductCategory.FURNITURE_LDSP


def test_stem_matches_word_start_only(classifier):
    # стем должен матчиться с начала слова: «покрывало» ловим, «одеколон» нет
    assert classifier.classify("Покрывала гобеленовые").matched
    assert classifier.classify("Одеколон и парфюмерия").matched is False
