"""Утилиты HTML-коннекторов: парс денег/дат, абсолютные ссылки.

Политика live-режима скраперов БЕЗ ключей: живой обход включается только
флагом DR_SCRAPE_LIVE=1 в .env (required_env скрап-коннекторов). По умолчанию —
mock на фикстурах: безопасно для тестов/CI и не дёргает чужие сайты случайно.
"""

from __future__ import annotations

import re
from datetime import datetime
from urllib.parse import urljoin

MONEY_RE = re.compile(r"[^\d,.\-]")


def parse_money(text: str | None) -> float | None:
    """«994 827,60 ₸» / «1 234 567.89» -> float. None, если чисел нет."""
    if not text:
        return None
    cleaned = MONEY_RE.sub("", text.replace("\xa0", ""))
    if not cleaned:
        return None
    # если и точка, и запятая — запятая почти наверняка десятичная в КЗ-форматах
    if "," in cleaned and "." in cleaned:
        cleaned = cleaned.replace(".", "").replace(",", ".")
    elif "," in cleaned:
        cleaned = cleaned.replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return None


DATE_FORMATS = ("%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M", "%d.%m.%Y", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d")


def parse_date(text: str | None) -> datetime | None:
    if not text:
        return None
    text = text.strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def clean_text(value: str | None) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def absolute(base: str, href: str | None) -> str:
    return urljoin(base, href or "")
