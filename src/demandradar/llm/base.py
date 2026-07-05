"""ИИ-слой. ОПЦИОНАЛЕН: ядро полностью работает без него.

Правила (жёсткие, из брифа):
  * Никаких платных облачных ИИ-API. Только: локальный Ollama,
    делегирование в локальный Claude Code, либо NullProvider (по умолчанию).
  * Любой вызов LLM обязан деградировать без падения: вернул None —
    вызывающий код обязан использовать эвристику.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class LLMProvider(ABC):
    """Единый интерфейс всех LLM-провайдеров."""

    name: str = "abstract"

    @abstractmethod
    def is_available(self) -> bool:
        """Доступен ли провайдер прямо сейчас (сервис запущен, модель на месте)."""

    @abstractmethod
    def complete(self, prompt: str, *, system: str | None = None, max_tokens: int = 512) -> str | None:
        """Свободная генерация. None = провайдер недоступен/ошибка (НЕ исключение)."""

    def classify(self, text: str, labels: list[str], *, instruction: str | None = None) -> str | None:
        """Выбрать один label для text. None = не смог/недоступен.

        Реализация по умолчанию — через complete(); провайдеры могут переопределить.
        """
        label_list = ", ".join(labels)
        prompt = (
            (instruction or "Классифицируй текст строго в одну из категорий.")
            + f"\nКатегории: {label_list}\n"
            + f"Текст: {text}\n"
            + "Ответь ТОЛЬКО названием категории, без пояснений."
        )
        answer = self.complete(prompt, max_tokens=32)
        if answer is None:
            return None
        answer = answer.strip().strip(".\"'").lower()
        for label in labels:
            if label.lower() == answer or label.lower() in answer:
                return label
        return None


class NullProvider(LLMProvider):
    """ИИ выключен: все «умные» функции деградируют до эвристик."""

    name = "null"

    def is_available(self) -> bool:
        return False

    def complete(self, prompt: str, *, system: str | None = None, max_tokens: int = 512) -> str | None:  # noqa: ARG002
        return None

    def classify(self, text: str, labels: list[str], *, instruction: str | None = None) -> str | None:  # noqa: ARG002
        return None
