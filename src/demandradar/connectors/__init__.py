"""Пакет коннекторов. Импорт пакета регистрирует все коннекторы в реестре."""

from demandradar.connectors import base  # noqa: F401
from demandradar.connectors.ecc import connector as _ecc  # noqa: F401
from demandradar.connectors.goszakup import connector as _goszakup  # noqa: F401
from demandradar.connectors.mitwork import connector as _mitwork  # noqa: F401
from demandradar.connectors.mpkz import connector as _mpkz  # noqa: F401
