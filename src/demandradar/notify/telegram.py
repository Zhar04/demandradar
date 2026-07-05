"""Telegram-уведомления. Без токена/в dry-run — печать в консоль (лог).

Отправка идёт через общий Fetcher (ретраи/троттлинг наследуются).
"""

from __future__ import annotations

import logging

from demandradar.core.models import DemandSignal
from demandradar.net.http import Fetcher, FetchError

logger = logging.getLogger(__name__)

CATEGORY_EMOJI = {
    "beds": "🛏",
    "mattresses": "🛌",
    "bedding": "🧺",
    "furniture_ldsp": "🗄",
    "kitchen": "🍽",
    "office_chairs": "💺",
    "racks": "🏗",
    "retail_equipment": "🏷",
    "showcases": "🪟",
    "mdf": "🪵",
    "turnkey": "🔑",
    "other": "📦",
}


def format_signal_message(signal: DemandSignal) -> str:
    lines = [f"{CATEGORY_EMOJI.get(signal.category.value, '📦')} <b>{signal.title}</b>"]
    if signal.budget:
        lines.append(f"💰 {signal.budget:,.0f} {signal.currency}".replace(",", " "))
    if signal.quantity:
        lines.append(f"📦 Кол-во: {signal.quantity:,.0f}".replace(",", " "))
    if signal.customer_name:
        customer = signal.customer_name
        if signal.customer_bin:
            customer += f" (БИН {signal.customer_bin})"
        lines.append(f"🏢 {customer}")
    if signal.region:
        lines.append(f"📍 {signal.region}")
    if signal.deadline:
        lines.append(f"⏳ Приём заявок до {signal.deadline:%d.%m.%Y %H:%M}")
    for contact in signal.contacts[:2]:
        parts = [p for p in (contact.name, contact.phone, contact.email) if p]
        if parts:
            lines.append(f"👤 {' · '.join(parts)}")
    lines.append(f"🔗 {signal.url}")
    lines.append(f"#{signal.source} #{signal.category.value} #{signal.demand_type.value}")
    return "\n".join(lines)


class TelegramNotifier:
    API_URL = "https://api.telegram.org"

    def __init__(self, fetcher: Fetcher, bot_token: str = "", chat_id: str = "", dry_run: bool = False):
        self.fetcher = fetcher
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.dry_run = dry_run

    @property
    def live(self) -> bool:
        return bool(self.bot_token and self.chat_id and not self.dry_run)

    def send_text(self, text: str) -> bool:
        """True = доставлено (или напечатано в dry-run). Ошибки сети не роняют пайплайн."""
        if not self.live:
            logger.info("[telegram dry-run]\n%s", text)
            return True
        try:
            response = self.fetcher.post(
                f"{self.API_URL}/bot{self.bot_token}/sendMessage",
                json={
                    "chat_id": self.chat_id,
                    "text": text,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                },
            )
            ok = response.status_code == 200 and response.json().get("ok") is True
            if not ok:
                logger.error("Telegram sendMessage failed: %s %.200s", response.status_code, response.text)
            return ok
        except (FetchError, ValueError) as exc:
            logger.error("Telegram send failed: %r", exc)
            return False

    def send_signal(self, signal: DemandSignal) -> bool:
        return self.send_text(format_signal_message(signal))
