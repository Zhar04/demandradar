"""Пакет коннекторов. Импорт пакета регистрирует все коннекторы в реестре."""

from demandradar.connectors import base  # noqa: F401
from demandradar.connectors.goszakup import connector as _goszakup  # noqa: F401
