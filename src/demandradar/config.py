"""Конфигурация: .env (секреты) + config/*.yaml (источники, категории).

Секреты НИКОГДА не хранятся в коде или git — только .env / переменные окружения.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = PROJECT_ROOT / "config"


@dataclass
class Settings:
    env: str = "dev"
    db_path: Path = PROJECT_ROOT / "data" / "demandradar.db"
    log_level: str = "INFO"

    # Секреты источников (пусто = mock-режим соответствующего коннектора)
    goszakup_token: str = ""
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    statsnet_token: str = ""
    kompra_token: str = ""
    adata_token: str = ""
    yandex_direct_token: str = ""
    hh_token: str = ""
    data_egov_apikey: str = ""

    # ИИ-слой (по умолчанию выключен)
    llm_provider: str = "null"
    ollama_model: str = "qwen3:8b"
    ollama_base_url: str = "http://localhost:11434"

    # Конфиги из YAML
    sources: dict = field(default_factory=dict)
    categories: dict = field(default_factory=dict)

    def env_value(self, name: str) -> str:
        """Значение env-переменной по имени (для проверки live-режима коннектора)."""
        return os.environ.get(name, "")


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def load_settings(dotenv_path: Path | None = None) -> Settings:
    load_dotenv(dotenv_path or PROJECT_ROOT / ".env")
    getenv = os.environ.get
    return Settings(
        env=getenv("DR_ENV", "dev"),
        db_path=Path(getenv("DR_DB_PATH", str(PROJECT_ROOT / "data" / "demandradar.db"))),
        log_level=getenv("DR_LOG_LEVEL", "INFO"),
        goszakup_token=getenv("GOSZAKUP_TOKEN", ""),
        telegram_bot_token=getenv("TELEGRAM_BOT_TOKEN", ""),
        telegram_chat_id=getenv("TELEGRAM_CHAT_ID", ""),
        statsnet_token=getenv("STATSNET_TOKEN", ""),
        kompra_token=getenv("KOMPRA_TOKEN", ""),
        adata_token=getenv("ADATA_TOKEN", ""),
        yandex_direct_token=getenv("YANDEX_DIRECT_TOKEN", ""),
        hh_token=getenv("HH_TOKEN", ""),
        data_egov_apikey=getenv("DATA_EGOV_APIKEY", ""),
        llm_provider=getenv("DR_LLM_PROVIDER", "null"),
        ollama_model=getenv("OLLAMA_MODEL", "qwen3:8b"),
        ollama_base_url=getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
        sources=_load_yaml(CONFIG_DIR / "sources.yaml"),
        categories=_load_yaml(CONFIG_DIR / "categories.yaml"),
    )
