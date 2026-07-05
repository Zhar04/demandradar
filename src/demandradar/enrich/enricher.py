"""ContactEnricher: цепочка провайдеров + кеш в БД.

Ошибка обогащения никогда не роняет пайплайн: сигнал уходит дальше без
дополнительного контакта.
"""

from __future__ import annotations

import logging

from demandradar.core.models import Contact, DemandSignal
from demandradar.enrich.base import CompanyProfile, EnrichmentProvider
from demandradar.storage.repo import CompanyCacheRepository

logger = logging.getLogger(__name__)


class ContactEnricher:
    def __init__(self, providers: list[EnrichmentProvider], cache: CompanyCacheRepository | None = None):
        self.providers = providers
        self.cache = cache

    def profile_for(self, bin_code: str) -> CompanyProfile | None:
        if self.cache is not None:
            cached = self.cache.get(bin_code)
            if cached is not None:
                return cached
        for provider in self.providers:
            try:
                profile = provider.lookup(bin_code)
            except Exception as exc:  # noqa: BLE001 — обогащение не критично
                logger.warning("Enrichment provider %s failed for %s: %r", provider.name, bin_code, exc)
                continue
            if profile is not None:
                if self.cache is not None:
                    self.cache.put(profile)
                return profile
        return None

    def enrich(self, signal: DemandSignal) -> bool:
        """Дописать контакт ЛПР в сигнал. True = контакт добавлен."""
        if not signal.customer_bin:
            return False
        profile = self.profile_for(signal.customer_bin)
        if profile is None:
            return False

        known_phones = {c.phone for c in signal.contacts if c.phone}
        known_emails = {c.email for c in signal.contacts if c.email}
        if profile.phone in known_phones or (profile.email and profile.email in known_emails):
            duplicate_channel = True
        else:
            duplicate_channel = False

        if not (profile.director or profile.phone or profile.email):
            return False
        if duplicate_channel and not profile.director:
            return False

        signal.contacts.append(
            Contact(
                name=profile.director,
                role="первый руководитель (реестр)",
                phone=profile.phone,
                email=profile.email,
                source=profile.source,
            )
        )
        return True
