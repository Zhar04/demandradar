"""Веб-дашборд DemandRadar: FastAPI + Jinja2 (серверный HTML, без CDN).

Запуск: python -m demandradar --serve [--port 8080]
Страницы: / (воронка+метрики), /signals (список+фильтры+смена статуса),
/connectors (мониторинг), /report (+ /report.csv), /health.
"""

from __future__ import annotations

import csv
import io
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import Depends, FastAPI, Form, Request
from fastapi.responses import RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from demandradar.config import Settings
from demandradar.core.models import ProductCategory, SignalStatus
from demandradar.storage.db import Database
from demandradar.storage.repo import ConnectorStateRepository, SignalRepository

TEMPLATES_DIR = Path(__file__).parent / "templates"

# Человекочитаемые названия статусов воронки (единственный источник — модель!)
STATUS_TITLES = {
    SignalStatus.NEW: "Новые",
    SignalStatus.IN_WORK: "В работе",
    SignalStatus.CONTACTED: "Контакт",
    SignalStatus.KP_SENT: "Отправлено КП",
    SignalStatus.MEETING: "Встреча",
    SignalStatus.DEAL: "Сделка",
    SignalStatus.REJECTED: "Отказ",
}

CATEGORY_TITLES = {
    "beds": "Кровати",
    "mattresses": "Матрасы",
    "bedding": "Постельное бельё",
    "furniture_ldsp": "ЛДСП-мебель",
    "kitchen": "Кухни",
    "office_chairs": "Кресла/стулья",
    "racks": "Стеллажи",
    "retail_equipment": "Торг. оборудование",
    "showcases": "Витрины",
    "mdf": "МДФ",
    "turnkey": "Под ключ",
    "other": "Прочее",
}

# «Коннектор жив», если последний успех был не давнее этого окна
ALIVE_WINDOW = timedelta(hours=24)


def create_app(settings: Settings) -> FastAPI:
    app = FastAPI(title="DemandRadar", docs_url=None, redoc_url=None)
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    templates.env.globals.update(
        status_titles=STATUS_TITLES,
        category_titles=CATEGORY_TITLES,
        statuses=list(SignalStatus),
        categories=list(ProductCategory),
    )
    app.state.started_at = datetime.now(UTC)

    def get_db():
        db = Database(settings.db_path)
        db.migrate()
        try:
            yield db
        finally:
            db.close()

    # -- страницы -------------------------------------------------------------

    @app.get("/")
    def index(request: Request, db: Database = Depends(get_db)):
        signals = SignalRepository(db)
        now = datetime.now(UTC)
        day_ago = (now - timedelta(days=1)).isoformat()
        week_ago = (now - timedelta(days=7)).isoformat()
        funnel = signals.funnel_counts()
        return templates.TemplateResponse(request, "index.html", {
            "funnel": funnel,
            "total": sum(funnel.values()),
            "count_day": signals.count_collected_since(day_ago),
            "count_week": signals.count_collected_since(week_ago),
            "count_all": signals.count(),
            "by_source": signals.breakdown("source"),
            "by_category": signals.breakdown("category"),
            "by_region": signals.breakdown("region")[:12],
            "by_demand": signals.breakdown("demand_type"),
            "top": signals.top_by_score(limit=10),
        })

    @app.get("/signals")
    def signals_page(
        request: Request,
        status: str = "",
        category: str = "",
        source: str = "",
        region: str = "",
        q: str = "",
        order: str = "score",
        db: Database = Depends(get_db),
    ):
        repo = SignalRepository(db)
        items = repo.list_filtered(
            status=status or None,
            category=category or None,
            source=source or None,
            region=region or None,
            search=q or None,
            order=order,
            limit=200,
        )
        return templates.TemplateResponse(request, "signals.html", {
            "items": items,
            "filters": {"status": status, "category": category, "source": source,
                        "region": region, "q": q, "order": order},
            "sources": [b["value"] for b in repo.breakdown("source")],
            "regions": [b["value"] for b in repo.breakdown("region")],
        })

    @app.post("/signals/{dedup_key}/status")
    def change_status(
        dedup_key: str,
        status: str = Form(...),
        back: str = Form("/signals"),
        db: Database = Depends(get_db),
    ):
        repo = SignalRepository(db)
        if repo.get(dedup_key) is not None and status in {s.value for s in SignalStatus}:
            repo.set_status(dedup_key, SignalStatus(status))
        return RedirectResponse(url=back or "/signals", status_code=303)

    @app.get("/connectors")
    def connectors_page(request: Request, db: Database = Depends(get_db)):
        states = ConnectorStateRepository(db).all_states()
        now = datetime.now(UTC)
        for state in states:
            last_success = state.get("last_success_at")
            alive = False
            if last_success:
                try:
                    alive = now - datetime.fromisoformat(last_success) <= ALIVE_WINDOW
                except ValueError:
                    alive = False
            state["alive"] = alive
        return templates.TemplateResponse(request, "connectors.html", {"states": states})

    @app.get("/watchlist")
    def watchlist_page(request: Request, db: Database = Depends(get_db)):
        from demandradar.watchlist.engine import build_profiles

        cards = build_profiles(SignalRepository(db), settings.watchlist)
        return templates.TemplateResponse(request, "watchlist.html", {"cards": cards})

    # -- МОДУЛЬ A: рефералы ------------------------------------------------------

    @app.get("/referrals")
    def referrals_page(request: Request, db: Database = Depends(get_db)):
        from demandradar.realestate.module import REF_STATUS_TITLES, RealEstateRepository, RefStatus

        contacts = RealEstateRepository(db).list_all()
        total_accrued = sum(c.referral_amount or 0 for c in contacts)
        return templates.TemplateResponse(request, "referrals.html", {
            "contacts": contacts,
            "ref_statuses": list(RefStatus),
            "ref_titles": REF_STATUS_TITLES,
            "total_accrued": total_accrued,
        })

    @app.post("/referrals/{contact_id}/update")
    def referral_update(
        contact_id: int,
        ref_status: str = Form(""),
        lead_signal_key: str = Form(""),
        deal_amount: str = Form(""),
        referral_pct: str = Form(""),
        db: Database = Depends(get_db),
    ):
        from demandradar.realestate.module import RealEstateRepository, RefStatus

        repo = RealEstateRepository(db)
        repo.update_referral(
            contact_id,
            ref_status=RefStatus(ref_status) if ref_status else None,
            lead_signal_key=lead_signal_key or None,
            deal_amount=float(deal_amount) if deal_amount else None,
            referral_pct=float(referral_pct) if referral_pct else None,
        )
        return RedirectResponse(url="/referrals", status_code=303)

    # -- очередь обзвона -----------------------------------------------------------

    @app.get("/callqueue.csv")
    def call_queue_csv(db: Database = Depends(get_db)):
        repo = SignalRepository(db)
        items = [
            s for s in repo.list_filtered(order="score", limit=500)
            if s.status in (SignalStatus.NEW, SignalStatus.IN_WORK) and s.contacts
        ]
        buffer = io.StringIO()
        writer = csv.writer(buffer, delimiter=";")
        writer.writerow(["score", "заголовок", "категория", "заказчик", "телефон", "email",
                         "контактное лицо", "регион", "бюджет", "срок", "ссылка"])
        for s in items:
            contact = next((c for c in s.contacts if c.phone or c.email), s.contacts[0])
            writer.writerow([
                s.score, s.title, CATEGORY_TITLES.get(s.category.value, s.category.value),
                s.customer_name or "", contact.phone or "", contact.email or "",
                contact.name or "", s.region or "", s.budget or "",
                s.deadline.strftime("%d.%m.%Y") if s.deadline else "", s.url,
            ])
        csv_bytes = ("﻿" + buffer.getvalue()).encode("utf-8")
        return Response(
            content=csv_bytes,
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="call_queue.csv"'},
        )

    # -- отчёт ------------------------------------------------------------------

    @app.get("/report")
    def report_page(request: Request, db: Database = Depends(get_db)):
        items = SignalRepository(db).active_for_report()
        total_budget = sum(s.budget or 0 for s in items)
        return templates.TemplateResponse(request, "report.html", {
            "items": items,
            "total_budget": total_budget,
            "generated_at": datetime.now(UTC),
            "started_at": app.state.started_at,
        })

    @app.get("/report.csv")
    def report_csv(db: Database = Depends(get_db)):
        items = SignalRepository(db).active_for_report()
        buffer = io.StringIO()
        writer = csv.writer(buffer, delimiter=";")
        writer.writerow([
            "score", "статус", "категория", "заголовок", "бюджет", "валюта",
            "кол-во", "заказчик", "БИН", "регион", "срок", "контакты",
            "источник", "ссылка", "собрано",
        ])
        for s in items:
            contacts = "; ".join(
                " ".join(p for p in (c.name, c.phone, c.email) if p) for c in s.contacts
            )
            writer.writerow([
                s.score, STATUS_TITLES.get(s.status, s.status.value),
                CATEGORY_TITLES.get(s.category.value, s.category.value), s.title,
                s.budget or "", s.currency, s.quantity or "",
                s.customer_name or "", s.customer_bin or "", s.region or "",
                s.deadline.strftime("%d.%m.%Y %H:%M") if s.deadline else "",
                contacts, s.source, s.url,
                s.collected_at.strftime("%d.%m.%Y %H:%M"),
            ])
        csv_bytes = ("﻿" + buffer.getvalue()).encode("utf-8")  # BOM для Excel
        stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M")
        return Response(
            content=csv_bytes,
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="demandradar_report_{stamp}.csv"'},
        )

    # -- сервисное ----------------------------------------------------------------

    @app.get("/health")
    def health(db: Database = Depends(get_db)):
        states = ConnectorStateRepository(db).all_states()
        return {
            "status": "ok",
            "started_at": app.state.started_at.isoformat(),
            "signals": SignalRepository(db).count(),
            "connectors": {
                s["connector_key"]: {
                    "last_success_at": s["last_success_at"],
                    "last_error": s["last_error"],
                }
                for s in states
            },
        }

    return app
