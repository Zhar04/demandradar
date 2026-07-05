"""Детерминированная классификация сигналов по товарным группам.

Правила из config/categories.yaml:
  * keywords — стемы-подстроки (регистронезависимо): "кроват" ловит
    «кровати», «кроватей» и т.д.;
  * negative_keywords — если совпали, категория отклоняется (защита от
    «шкаф управления», «кресло-коляска»);
  * tru_prefixes — префиксы кодов ЕНС ТРУ лота; совпадение кода — сильный
    признак, работает даже без ключевых слов.

ИИ здесь НЕ обязателен: LLM (если включён) подключается ПОЗЖЕ и только для
лотов, где эвристика ничего не нашла, а global_keywords дали слабый сигнал.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from demandradar.core.models import ProductCategory


@dataclass
class MatchResult:
    matched: bool
    category: ProductCategory = ProductCategory.OTHER
    keywords: list[str] = field(default_factory=list)
    codes: list[str] = field(default_factory=list)


def _hits(text_lower: str, stems: list[str]) -> list[str]:
    """Стемы, найденные в тексте по началу слова («кроват» -> «Кровати»)."""
    return [s for s in stems if re.search(r"\b" + re.escape(s.lower()), text_lower)]


class Classifier:
    def __init__(self, categories_config: dict):
        self._categories: dict[str, dict] = categories_config.get("categories", {})
        self._global_keywords: list[str] = [
            k.lower() for k in categories_config.get("global_keywords", [])
        ]

    def classify(self, text: str, tru_codes: list[str] | None = None) -> MatchResult:
        """Определить релевантность и категорию по тексту и кодам ЕНС ТРУ."""
        text_lower = text.lower()
        tru_codes = [str(c) for c in (tru_codes or [])]

        best: MatchResult | None = None
        best_strength = 0
        vetoed = False  # какая-то категория совпала по словам, но отбита негативом

        for key, config in self._categories.items():
            hit_keywords = _hits(text_lower, config.get("keywords", []))
            hit_negative = _hits(text_lower, config.get("negative_keywords", []))
            hit_codes = [
                code
                for code in tru_codes
                if any(code.startswith(prefix) for prefix in config.get("tru_prefixes", []))
            ]

            if hit_negative and not hit_codes:
                # слова совпали, но негатив без подтверждения кодом — отклоняем;
                # и блокируем global-сеть, чтобы «кресло-коляска» не пролезла как other
                if hit_keywords:
                    vetoed = True
                continue

            # сила совпадения: код ТРУ весит как 2 ключевых слова
            strength = len(hit_keywords) + 2 * len(hit_codes)
            if strength > best_strength:
                best_strength = strength
                best = MatchResult(
                    matched=True,
                    category=ProductCategory(key),
                    keywords=hit_keywords,
                    codes=hit_codes,
                )

        if best is not None:
            return best
        if vetoed:
            return MatchResult(matched=False)

        # Ни одна категория не совпала — широкая сеть global_keywords -> OTHER
        global_hits = _hits(text_lower, self._global_keywords)
        if global_hits:
            return MatchResult(matched=True, category=ProductCategory.OTHER, keywords=global_hits)

        return MatchResult(matched=False)
