"""Реализации провайдеров обогащения."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from demandradar.enrich.base import CompanyProfile

logger = logging.getLogger(__name__)

FIXTURES_PATH = Path(__file__).parent / "fixtures" / "companies.json"


class MockRegistryProvider:
    """Mock-реестр компаний на фикстурах (формат близок к data.egov gbd_ul)."""

    name = "mock_registry"

    def __init__(self, fixtures_path: Path | None = None):
        self._path = fixtures_path or FIXTURES_PATH
        self._data: dict[str, dict] | None = None

    def _load(self) -> dict[str, dict]:
        if self._data is None:
            try:
                self._data = json.loads(self._path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                logger.warning("MockRegistryProvider: cannot read fixtures: %r", exc)
                self._data = {}
        return self._data

    def lookup(self, bin_code: str) -> CompanyProfile | None:
        item = self._load().get(bin_code)
        if item is None:
            return None
        return CompanyProfile(bin=bin_code, source=self.name, **item)
