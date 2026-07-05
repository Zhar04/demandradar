"""Тесты ИИ-слоя: Null по умолчанию, деградация без падений, фабрика."""

from demandradar.llm.base import LLMProvider, NullProvider
from demandradar.llm.claude_code import ClaudeCodeProvider
from demandradar.llm.factory import create_llm_provider
from demandradar.llm.ollama import OllamaProvider


def test_null_provider_is_default():
    provider = create_llm_provider("null")
    assert isinstance(provider, NullProvider)
    assert provider.complete("привет") is None
    assert provider.classify("кровати", ["beds", "other"]) is None
    assert provider.is_available() is False


def test_factory_unknown_falls_back_to_null():
    assert isinstance(create_llm_provider("gpt-99-paid-api"), NullProvider)
    assert isinstance(create_llm_provider(""), NullProvider)


def test_factory_creates_ollama():
    provider = create_llm_provider("ollama", ollama_model="qwen3:4b")
    assert isinstance(provider, OllamaProvider)
    assert provider.model == "qwen3:4b"


def test_factory_creates_claude_code():
    assert isinstance(create_llm_provider("claude_code"), ClaudeCodeProvider)


def test_classify_via_complete():
    class FakeProvider(LLMProvider):
        name = "fake"

        def is_available(self):
            return True

        def complete(self, prompt, *, system=None, max_tokens=512):
            return "  Beds. "

    assert FakeProvider().classify("кровати металлические", ["beds", "other"]) == "beds"


def test_classify_returns_none_on_garbage():
    class GarbageProvider(LLMProvider):
        name = "garbage"

        def is_available(self):
            return True

        def complete(self, prompt, *, system=None, max_tokens=512):
            return "не могу определить"

    assert GarbageProvider().classify("текст", ["beds", "other"]) is None
