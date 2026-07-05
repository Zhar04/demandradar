"""Выбор LLM-провайдера по конфигу. По умолчанию — NullProvider (ИИ выключен)."""

from __future__ import annotations

import logging

from demandradar.llm.base import LLMProvider, NullProvider
from demandradar.llm.claude_code import ClaudeCodeProvider
from demandradar.llm.ollama import OllamaProvider

logger = logging.getLogger(__name__)


def create_llm_provider(provider_name: str, *, ollama_base_url: str = "http://localhost:11434",
                        ollama_model: str = "qwen3:8b") -> LLMProvider:
    name = (provider_name or "null").strip().lower()
    if name == "ollama":
        return OllamaProvider(base_url=ollama_base_url, model=ollama_model)
    if name == "claude_code":
        return ClaudeCodeProvider()
    if name != "null":
        logger.warning("Unknown DR_LLM_PROVIDER=%r, falling back to null (AI disabled)", provider_name)
    return NullProvider()
