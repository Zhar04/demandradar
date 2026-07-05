"""Дневной дайджест: сводка за период + топ по приоритету одним сообщением."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from demandradar.dashboard.app import CATEGORY_TITLES
from demandradar.storage.repo import SignalRepository


def build_digest(repo: SignalRepository, *, hours: int = 24, top_n: int = 7) -> str:
    since = datetime.now(UTC) - timedelta(hours=hours)
    since_iso = since.isoformat()
    count = repo.count_collected_since(since_iso)

    lines = [f"📡 <b>DemandRadar — дайджест за {hours} ч</b>",
             f"Новых сигналов: <b>{count}</b> (всего в базе {repo.count()})"]

    by_category = [b for b in repo.breakdown("category", since_iso) if b["n"] > 0]
    if by_category:
        cats = " · ".join(
            f"{CATEGORY_TITLES.get(b['value'], b['value'])}: {b['n']}" for b in by_category[:6]
        )
        lines.append(cats)

    top = repo.top_by_score(limit=top_n, since_iso=since_iso)
    if top:
        lines.append("")
        lines.append("🔥 <b>Топ по приоритету:</b>")
        for i, s in enumerate(top, 1):
            budget = f" — {s.budget:,.0f} ₸".replace(",", " ") if s.budget else ""
            region = f" ({s.region})" if s.region else ""
            lines.append(f"{i}. [{s.score}] <a href=\"{s.url}\">{s.title}</a>{budget}{region}")
    else:
        lines.append("За период нет новых релевантных сигналов.")

    return "\n".join(lines)
