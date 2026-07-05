"""Тесты сетевого слоя: ретраи, backoff, троттлинг, UA, прокси-точка."""

import httpx
import pytest

from demandradar.net.http import Fetcher, FetcherConfig, FetchError
from demandradar.net.proxy import NoProxyProvider


def fast_config(**overrides) -> FetcherConfig:
    """Конфиг без задержек, чтобы тесты не спали."""
    defaults = dict(max_retries=2, backoff_base=0.0, backoff_max=0.0, min_host_interval=0.0)
    defaults.update(overrides)
    return FetcherConfig(**defaults)


def make_fetcher(handler, **cfg) -> Fetcher:
    return Fetcher(config=fast_config(**cfg), transport=httpx.MockTransport(handler))


def test_success_passthrough():
    def handler(request):
        return httpx.Response(200, json={"ok": True})

    with make_fetcher(handler) as fetcher:
        assert fetcher.get_json("https://example.kz/api") == {"ok": True}


def test_retries_on_5xx_then_success():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(503)
        return httpx.Response(200, json={"ok": True})

    with make_fetcher(handler) as fetcher:
        assert fetcher.get_json("https://example.kz/api") == {"ok": True}
    assert calls["n"] == 3


def test_gives_up_after_max_retries():
    def handler(request):
        return httpx.Response(500)

    with make_fetcher(handler) as fetcher:
        with pytest.raises(FetchError) as excinfo:
            fetcher.get("https://example.kz/api")
    assert excinfo.value.status_code == 500


def test_no_retry_on_4xx():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(404)

    with make_fetcher(handler) as fetcher:
        response = fetcher.get("https://example.kz/missing")
    assert response.status_code == 404
    assert calls["n"] == 1  # 404 не ретраится


def test_retries_on_network_error():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("boom")
        return httpx.Response(200, json={})

    with make_fetcher(handler) as fetcher:
        fetcher.get("https://example.kz/api")
    assert calls["n"] == 2


def test_user_agent_set_from_pool():
    seen = {}

    def handler(request):
        seen["ua"] = request.headers.get("User-Agent")
        return httpx.Response(200)

    config = fast_config()
    with make_fetcher(handler) as fetcher:
        fetcher.get("https://example.kz/")
    assert seen["ua"] in config.user_agents


def test_custom_headers_override_ua():
    seen = {}

    def handler(request):
        seen["ua"] = request.headers.get("User-Agent")
        return httpx.Response(200)

    with make_fetcher(handler) as fetcher:
        fetcher.get("https://example.kz/", headers={"User-Agent": "DemandRadar/1.0"})
    assert seen["ua"] == "DemandRadar/1.0"


def test_no_proxy_provider_default():
    provider = NoProxyProvider()
    assert provider.get_proxy() is None
    provider.report_failure(None)  # не должно падать
