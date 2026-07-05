"""Единый сетевой слой. ВСЕ коннекторы ходят в сеть только через Fetcher.

Возможности:
  * таймауты (connect/read) из конфига;
  * ретраи с экспоненциальным backoff + джиттер (сетевые ошибки, 429, 5xx);
  * уважение заголовка Retry-After;
  * троттлинг: минимальный интервал между запросами к одному хосту;
  * ротация User-Agent;
  * точка расширения ProxyProvider (по умолчанию NoProxyProvider).
"""

from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import httpx

from demandradar.net.proxy import NoProxyProvider, ProxyProvider

logger = logging.getLogger(__name__)

# Реалистичные десктопные UA; ротация снижает шанс тупых блокировок.
DEFAULT_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:127.0) Gecko/20100101 Firefox/127.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
]

RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class FetchError(Exception):
    """Запрос окончательно провалился после всех ретраев."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


@dataclass
class FetcherConfig:
    connect_timeout: float = 10.0
    read_timeout: float = 30.0
    max_retries: int = 4
    backoff_base: float = 1.5      # секунды; задержка = base * 2^attempt + jitter
    backoff_max: float = 60.0
    min_host_interval: float = 1.0  # сек между запросами к одному хосту (троттлинг)
    user_agents: list[str] = field(default_factory=lambda: list(DEFAULT_USER_AGENTS))


class Fetcher:
    """Синхронный HTTP-клиент с надёжностью, достаточной для 24/7."""

    def __init__(
        self,
        config: FetcherConfig | None = None,
        proxy_provider: ProxyProvider | None = None,
        transport: httpx.BaseTransport | None = None,
    ):
        self.config = config or FetcherConfig()
        self.proxy = proxy_provider or NoProxyProvider()
        self._last_request_at: dict[str, float] = {}  # host -> monotonic time
        self._transport = transport  # для тестов (httpx.MockTransport)
        self._client: httpx.Client | None = None

    # -- lifecycle ---------------------------------------------------------

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            timeout = httpx.Timeout(
                connect=self.config.connect_timeout,
                read=self.config.read_timeout,
                write=self.config.read_timeout,
                pool=self.config.connect_timeout,
            )
            proxy = self.proxy.get_proxy()
            self._client = httpx.Client(
                timeout=timeout,
                follow_redirects=True,
                transport=self._transport,
                proxy=proxy,
            )
        return self._client

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def __enter__(self) -> Fetcher:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- throttling --------------------------------------------------------

    def _throttle(self, url: str) -> None:
        host = urlsplit(url).netloc
        last = self._last_request_at.get(host)
        if last is not None:
            elapsed = time.monotonic() - last
            wait = self.config.min_host_interval - elapsed
            if wait > 0:
                time.sleep(wait)
        self._last_request_at[host] = time.monotonic()

    # -- core request ------------------------------------------------------

    def request(
        self,
        method: str,
        url: str,
        *,
        params: dict | None = None,
        json: dict | None = None,
        data: dict | None = None,
        headers: dict | None = None,
    ) -> httpx.Response:
        cfg = self.config
        merged_headers = {"User-Agent": random.choice(cfg.user_agents)}
        if headers:
            merged_headers.update(headers)

        last_error: Exception | None = None
        for attempt in range(cfg.max_retries + 1):
            self._throttle(url)
            try:
                response = self._get_client().request(
                    method, url, params=params, json=json, data=data, headers=merged_headers
                )
            except httpx.HTTPError as exc:
                last_error = exc
                self.proxy.report_failure(self.proxy.get_proxy())
                self._sleep_backoff(attempt, None, url, repr(exc))
                continue

            if response.status_code in RETRYABLE_STATUS:
                last_error = FetchError(
                    f"HTTP {response.status_code} from {url}", status_code=response.status_code
                )
                retry_after = _parse_retry_after(response)
                self._sleep_backoff(attempt, retry_after, url, f"HTTP {response.status_code}")
                continue

            return response

        raise FetchError(
            f"{method} {url} failed after {cfg.max_retries + 1} attempts: {last_error}",
            status_code=getattr(last_error, "status_code", None),
        )

    def _sleep_backoff(self, attempt: int, retry_after: float | None, url: str, reason: str) -> None:
        cfg = self.config
        if attempt >= cfg.max_retries:
            return  # последняя попытка исчерпана — request() бросит FetchError
        delay = retry_after if retry_after is not None else cfg.backoff_base * (2**attempt)
        delay = min(delay, cfg.backoff_max) + random.uniform(0, 0.5)
        logger.warning("Retry %s/%s for %s (%s), sleeping %.1fs", attempt + 1, cfg.max_retries, url, reason, delay)
        time.sleep(delay)

    # -- helpers -----------------------------------------------------------

    def get(self, url: str, **kwargs) -> httpx.Response:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs) -> httpx.Response:
        return self.request("POST", url, **kwargs)

    def get_json(self, url: str, **kwargs) -> dict | list:
        response = self.get(url, **kwargs)
        response.raise_for_status()
        return response.json()

    def post_json(self, url: str, **kwargs) -> dict | list:
        response = self.post(url, **kwargs)
        response.raise_for_status()
        return response.json()


def _parse_retry_after(response: httpx.Response) -> float | None:
    value = response.headers.get("Retry-After")
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        return None
