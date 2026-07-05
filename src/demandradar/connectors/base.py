"""Интерфейс Connector и реестр коннекторов.

Добавить источник = написать один модуль-коннектор и зарегистрировать его
декоратором @register. Ядро о конкретных источниках ничего не знает.

Каждый коннектор обязан:
  * ходить в сеть ТОЛЬКО через переданный Fetcher (сетевой слой);
  * поддерживать mock-режим (фикстуры в tests/fixtures или рядом с коннектором),
    автоматически включающийся, когда нет живого ключа/доступа;
  * не бросать исключение на битом элементе: пропускать и логировать.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections.abc import Iterable, Iterator
from datetime import datetime
from enum import StrEnum

from demandradar.core.models import DemandSignal, DemandType
from demandradar.net.http import Fetcher

logger = logging.getLogger(__name__)


class ConnectorMode(StrEnum):
    MOCK = "mock"  # работа на записанных фикстурах (нет ключа/доступа)
    LIVE = "live"  # реальные запросы к источнику


class Connector(ABC):
    """Базовый класс коннектора-источника."""

    #: уникальный ключ источника (совпадает с config/sources.yaml)
    key: str = "abstract"
    #: какой тип спроса приносит источник
    demand_type: DemandType = DemandType.FORMALIZED
    #: имена env-переменных, необходимых для live-режима (пусто = live без ключа)
    required_env: tuple[str, ...] = ()

    def __init__(self, fetcher: Fetcher, mode: ConnectorMode):
        self.fetcher = fetcher
        self.mode = mode

    @classmethod
    def from_settings(cls, fetcher: Fetcher, settings) -> Connector:
        """Собрать коннектор из настроек. Режим: LIVE, если все required_env заданы.

        Коннекторы с доп. параметрами (токен и т.п.) переопределяют этот метод.
        """
        live = all(settings.env_value(name) for name in cls.required_env)
        return cls(fetcher, ConnectorMode.LIVE if live else ConnectorMode.MOCK)

    # -- обязательная часть -------------------------------------------------

    @abstractmethod
    def fetch(self, since: datetime | None = None) -> Iterable[dict]:
        """Достать сырые элементы источника (после `since`, если задано).

        В mock-режиме — читает фикстуры. Возвращает сырые dict'ы как есть.
        """

    @abstractmethod
    def normalize(self, raw: dict) -> DemandSignal | None:
        """Привести сырой элемент к DemandSignal. None = элемент не о закупке/битый."""

    # -- общая логика ---------------------------------------------------------

    def collect(self, since: datetime | None = None) -> Iterator[DemandSignal]:
        """fetch -> normalize с изоляцией ошибок по элементам."""
        for raw in self.fetch(since=since):
            try:
                signal = self.normalize(raw)
            except Exception:  # noqa: BLE001 - один битый элемент не роняет сбор
                logger.exception("[%s] failed to normalize item, skipping: %.200r", self.key, raw)
                continue
            if signal is not None:
                yield signal


# -- реестр -------------------------------------------------------------------

_REGISTRY: dict[str, type[Connector]] = {}


def register(cls: type[Connector]) -> type[Connector]:
    """Декоратор регистрации коннектора по его key."""
    if cls.key in _REGISTRY:
        raise ValueError(f"Duplicate connector key: {cls.key!r}")
    _REGISTRY[cls.key] = cls
    return cls


def get_connector_class(key: str) -> type[Connector]:
    try:
        return _REGISTRY[key]
    except KeyError:
        raise KeyError(f"Unknown connector {key!r}. Registered: {sorted(_REGISTRY)}") from None


def all_connector_keys() -> list[str]:
    return sorted(_REGISTRY)
