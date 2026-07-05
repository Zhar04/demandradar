"""Локальный LLM через Ollama (http://localhost:11434). Бесплатно, офлайн.

Рекомендуемая модель: qwen3:8b (хороший баланс ума/скорости для RU-текстов);
на слабом железе — qwen3:4b. Модель задаётся в .env (OLLAMA_MODEL).
"""

from __future__ import annotations

import logging

import httpx

from demandradar.llm.base import LLMProvider

logger = logging.getLogger(__name__)


class OllamaProvider(LLMProvider):
    name = "ollama"

    def __init__(self, base_url: str = "http://localhost:11434", model: str = "qwen3:8b", timeout: float = 120.0):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout

    def is_available(self) -> bool:
        try:
            response = httpx.get(f"{self.base_url}/api/tags", timeout=3.0)
            return response.status_code == 200
        except httpx.HTTPError:
            return False

    def complete(self, prompt: str, *, system: str | None = None, max_tokens: int = 512) -> str | None:
        payload: dict = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {"num_predict": max_tokens, "temperature": 0.1},
        }
        if system:
            payload["system"] = system
        try:
            response = httpx.post(f"{self.base_url}/api/generate", json=payload, timeout=self.timeout)
            response.raise_for_status()
            return response.json().get("response")
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            logger.warning("Ollama call failed (degrading to heuristics): %r", exc)
            return None
