"""Модуль ТОЧЕЧНОЙ рассылки (не массовой!).

Правила (бриф, юр. рамки):
  * только бизнес-контакты, только по конкретному сигналу/партнёру;
  * отправка требует ЯВНОГО подтверждения (--confirm); без него — черновик;
  * каждая попытка (draft и sent) логируется в outreach_log;
  * в каждом письме — строка отписки;
  * email через SMTP из .env (SMTP_*); нет настроек = только черновики.
"""

from __future__ import annotations

import logging
import smtplib
from datetime import UTC, datetime
from email.message import EmailMessage

from demandradar.core.models import DemandSignal
from demandradar.storage.db import Database

logger = logging.getLogger(__name__)

UNSUBSCRIBE_FOOTER = (
    "\n---\n"
    "Вы получили это письмо, потому что ваша организация опубликовала закупку/объявление "
    "или контакт указан в открытых источниках. Если письма не нужны — ответьте "
    "«отписаться», и мы больше не побеспокоим."
)

CATEGORY_PITCH = {
    "beds": "кровати (металл/ЛДСП) и матрасы напрямую от заводов КЗ/РФ/Китая",
    "mattresses": "матрасы любого класса, включая люкс, напрямую от производителей",
    "bedding": "постельное бельё оптом от фабрик",
    "furniture_ldsp": "ЛДСП-мебель (шкафы, столы, тумбы) под ваш размер",
    "kitchen": "кухонные гарнитуры под проект",
    "office_chairs": "офисные кресла и стулья от производителей",
    "racks": "складские и архивные стеллажи с монтажом",
    "retail_equipment": "торговое оборудование: ценникодержатели, крючки, экономпанели",
    "showcases": "витрины торговые, музейные и ювелирные",
    "mdf": "МДФ-панели оптом",
    "turnkey": "оснащение объекта «под ключ» — от аптек до ювелирных салонов",
    "other": "поставку по вашей заявке напрямую от производителей",
}


def build_signal_draft(signal: DemandSignal, sender_name: str = "DemandRadar") -> tuple[str, str]:
    """(subject, body) для точечного письма по сигналу."""
    pitch = CATEGORY_PITCH.get(signal.category.value, CATEGORY_PITCH["other"])
    subject = f"По вашей закупке: {signal.title[:80]}"
    contact_name = next((c.name for c in signal.contacts if c.name), None)
    greeting = f"Здравствуйте, {contact_name}!" if contact_name else "Здравствуйте!"
    lines = [
        greeting,
        "",
        f"Видим вашу актуальную потребность: «{signal.title}»"
        + (f" (бюджет ~{signal.budget:,.0f} {signal.currency})".replace(",", " ") if signal.budget else "") + ".",
        f"Мы поставляем {pitch}: прямые контракты с заводами КЗ, РФ, Китая, Турции — "
        "без посредников, с доставкой и документами для тендеров.",
        "",
        "Готовы за 1 день дать расчёт под вашу спецификацию. Ответьте на письмо "
        "или позвоните — приедем с образцами.",
        "",
        f"— {sender_name}",
        f"Ссылка на вашу закупку: {signal.url}",
        UNSUBSCRIBE_FOOTER,
    ]
    return subject, "\n".join(lines)


class OutreachService:
    def __init__(self, db: Database, *, smtp_host: str = "", smtp_port: int = 465,
                 smtp_user: str = "", smtp_password: str = "", smtp_from: str = ""):
        self.db = db
        self.smtp_host = smtp_host
        self.smtp_port = smtp_port
        self.smtp_user = smtp_user
        self.smtp_password = smtp_password
        self.smtp_from = smtp_from or smtp_user

    @property
    def can_send(self) -> bool:
        return bool(self.smtp_host and self.smtp_user and self.smtp_password)

    def _log(self, *, channel: str, recipient: str, subject: str | None, body: str,
             signal_key: str | None, status: str) -> int:
        with self.db.conn:
            cursor = self.db.conn.execute(
                "INSERT INTO outreach_log (created_at, channel, recipient, subject, body, signal_key, status) "
                "VALUES (?,?,?,?,?,?,?)",
                (datetime.now(UTC).isoformat(), channel, recipient, subject, body, signal_key, status),
            )
        return cursor.lastrowid

    def send_email(self, *, to: str, subject: str, body: str,
                   signal_key: str | None = None, confirm: bool = False) -> str:
        """Возвращает статус: 'draft' (не подтверждено/нет SMTP) или 'sent'."""
        if not confirm:
            self._log(channel="email", recipient=to, subject=subject, body=body,
                      signal_key=signal_key, status="draft")
            logger.info("Outreach draft for %s logged (no --confirm, nothing sent)", to)
            return "draft"
        if not self.can_send:
            self._log(channel="email", recipient=to, subject=subject, body=body,
                      signal_key=signal_key, status="draft")
            logger.warning("SMTP не настроен (.env SMTP_*) — письмо сохранено как черновик")
            return "draft"

        message = EmailMessage()
        message["From"] = self.smtp_from
        message["To"] = to
        message["Subject"] = subject
        message.set_content(body)
        if self.smtp_port == 465:
            with smtplib.SMTP_SSL(self.smtp_host, self.smtp_port, timeout=30) as server:
                server.login(self.smtp_user, self.smtp_password)
                server.send_message(message)
        else:
            with smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=30) as server:
                server.starttls()
                server.login(self.smtp_user, self.smtp_password)
                server.send_message(message)
        self._log(channel="email", recipient=to, subject=subject, body=body,
                  signal_key=signal_key, status="sent")
        logger.info("Outreach email SENT to %s", to)
        return "sent"

    def history(self, limit: int = 100) -> list[dict]:
        rows = self.db.conn.execute(
            "SELECT * FROM outreach_log ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]
