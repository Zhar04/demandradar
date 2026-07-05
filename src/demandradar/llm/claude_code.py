"""Делегирование ИИ-задач локальному Claude Code CLI пользователя.

Работает через `claude -p "<prompt>"` (headless-режим CLI). Это использует
подписку/установку Claude Code на машине пользователя; софт сам НЕ ходит
ни в какие платные API. Подключение описано в README (раздел «ИИ-слой»).
"""

from __future__ import annotations

import logging
import shutil
import subprocess

from demandradar.llm.base import LLMProvider

logger = logging.getLogger(__name__)


class ClaudeCodeProvider(LLMProvider):
    name = "claude_code"

    def __init__(self, binary: str = "claude", timeout: float = 180.0):
        self.binary = binary
        self.timeout = timeout

    def is_available(self) -> bool:
        return shutil.which(self.binary) is not None

    def complete(self, prompt: str, *, system: str | None = None, max_tokens: int = 512) -> str | None:  # noqa: ARG002
        if not self.is_available():
            return None
        full_prompt = f"{system}\n\n{prompt}" if system else prompt
        try:
            result = subprocess.run(
                [self.binary, "-p", full_prompt],
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=self.timeout,
                check=False,
            )
            if result.returncode != 0:
                logger.warning("claude CLI exited %s: %s", result.returncode, result.stderr[:200])
                return None
            return result.stdout.strip() or None
        except (OSError, subprocess.TimeoutExpired) as exc:
            logger.warning("Claude Code call failed (degrading to heuristics): %r", exc)
            return None
