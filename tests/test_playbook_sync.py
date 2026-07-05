"""DoD-тест: плейбук синхронизирован с кодом (статусы воронки и реф-статусы)."""

from pathlib import Path

from demandradar.core.models import SignalStatus
from demandradar.dashboard.app import STATUS_TITLES
from demandradar.realestate.module import REF_STATUS_TITLES, RefStatus

PLAYBOOK = (Path(__file__).parents[1] / "docs" / "PLAYBOOK.md").read_text(encoding="utf-8")


def test_all_funnel_statuses_documented():
    for status in SignalStatus:
        assert f"`{status.value}`" in PLAYBOOK, (
            f"Статус {status.value} есть в коде, но не описан в PLAYBOOK.md — "
            "синхронизируй (Definition of Done)"
        )
        assert STATUS_TITLES[status] in PLAYBOOK


def test_referral_statuses_documented():
    # русские названия реф-статусов из дашборда должны быть в плейбуке
    for status in (RefStatus.CANDIDATE, RefStatus.AGREED, RefStatus.DEAL_CLOSED, RefStatus.PAID):
        assert REF_STATUS_TITLES[status] in PLAYBOOK


def test_scoring_speed_rule_matches_config():
    # правило скорости в плейбуке ссылается на пороги 80/55 и half-life 7 дней
    assert "80" in PLAYBOOK and "55" in PLAYBOOK
    assert "7 дней" in PLAYBOOK or "half_life" in PLAYBOOK or "полураспада" in PLAYBOOK
