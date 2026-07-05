"""Точка расширения под пул прокси / IP-ротацию.

Сейчас используется NoProxyProvider. Когда появится пул прокси —
реализуем новый ProxyProvider (например RotatingProxyProvider) и
подключим его в Fetcher через конфиг, БЕЗ правки коннекторов.
"""

from __future__ import annotations

from typing import Protocol


class ProxyProvider(Protocol):
    """Интерфейс поставщика прокси для сетевого слоя."""

    def get_proxy(self) -> str | None:
        """Вернуть URL прокси (http://user:pass@host:port) или None (без прокси)."""
        ...

    def report_failure(self, proxy: str | None) -> None:
        """Сообщить, что запрос через данный прокси провалился (для ротации/бана)."""
        ...


class NoProxyProvider:
    """По умолчанию: работаем без прокси."""

    def get_proxy(self) -> str | None:
        return None

    def report_failure(self, proxy: str | None) -> None:  # noqa: ARG002
        return None
