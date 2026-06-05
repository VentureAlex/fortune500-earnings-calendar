"""
Fortune 500 Earnings Calendar — FastAPI backend.

Serves JSON for the FullCalendar frontend and static HTML in local dev.
On Vercel: static files are served by Vercel CDN; only the /api/* routes
           hit this serverless function.
Locally:   StaticFiles mount at "/" makes `python run.py` self-contained.

Scaling path: swap SQLite for Postgres by changing DATABASE_PATH to a
              connection string and the sqlite3 calls to asyncpg/SQLAlchemy.
"""

from __future__ import annotations

import logging
import os
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from mangum import Mangum

from .database import ensure_seeded, get_db

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(name)s  %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Fortune 500 Earnings Calendar",
    description="Earnings dates for Fortune 500 companies",
    version="1.0.0",
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def startup_event():
    ensure_seeded()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_INDUSTRY_COLORS: dict[str, str] = {
    "Technology":              "#6366f1",  # indigo
    "Healthcare":              "#10b981",  # emerald
    "Financials":              "#f59e0b",  # amber
    "Energy":                  "#ef4444",  # red
    "Retail":                  "#8b5cf6",  # violet
    "Consumer":                "#ec4899",  # pink
    "Automotive":              "#14b8a6",  # teal
    "Telecommunications":      "#3b82f6",  # blue
    "Media":                   "#f97316",  # orange
    "Industrial":              "#64748b",  # slate
    "Aerospace":               "#0ea5e9",  # sky
    "Logistics":               "#a855f7",  # purple
    "Real Estate":             "#84cc16",  # lime
    "default":                 "#6b7280",  # gray
}


def _color_for(industry: str | None) -> str:
    for key, color in _INDUSTRY_COLORS.items():
        if key.lower() in (industry or "").lower():
            return color
    return _INDUSTRY_COLORS["default"]


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/api/health")
def health():
    return {"status": "ok", "version": "1.0.0"}


@app.get("/api/companies")
def list_companies():
    """All companies — used for ticker autocomplete and industry filter."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT ticker, name, industry, rank FROM companies ORDER BY rank"
        ).fetchall()
    return [dict(r) for r in rows]


@app.get("/api/earnings")
def list_earnings(
    start: Optional[str] = Query(None, description="ISO date, default today"),
    end: Optional[str] = Query(None, description="ISO date, default today+365"),
    ticker: Optional[str] = Query(None, description="Comma-separated tickers"),
    year: Optional[int] = Query(None, description="Fiscal year"),
    quarter: Optional[str] = Query(None, description="Q1 / Q2 / Q3 / Q4"),
    industry: Optional[str] = Query(None, description="Partial match on industry"),
):
    """
    Return earnings events formatted for FullCalendar.

    FullCalendar calls this with `start` / `end` on each calendar navigation.
    Additional filters narrow results for the sidebar filters.
    """
    today = date.today()
    start_date = start or today.isoformat()
    end_date = end or (today + timedelta(days=365)).isoformat()

    sql = """
        SELECT e.id, e.ticker, e.date, e.quarter, e.fiscal_year,
               e.eps_estimate, e.actual_eps, e.source,
               c.name, c.industry, c.rank
        FROM earnings e
        LEFT JOIN companies c ON e.ticker = c.ticker
        WHERE e.date >= ? AND e.date <= ?
    """
    params: list = [start_date, end_date]

    if ticker:
        tickers = [t.strip().upper() for t in ticker.split(",") if t.strip()]
        if tickers:
            placeholders = ",".join("?" * len(tickers))
            sql += f" AND e.ticker IN ({placeholders})"
            params.extend(tickers)

    if year:
        sql += " AND e.fiscal_year = ?"
        params.append(year)

    if quarter:
        sql += " AND e.quarter = ?"
        params.append(quarter.upper())

    if industry:
        sql += " AND c.industry LIKE ?"
        params.append(f"%{industry}%")

    sql += " ORDER BY e.date, c.rank"

    with get_db() as conn:
        rows = conn.execute(sql, params).fetchall()

    events = []
    for row in rows:
        r = dict(row)
        company = r.get("name") or r["ticker"]
        title = r["ticker"]
        if r.get("quarter"):
            title += f" {r['quarter']}"

        color = _color_for(r.get("industry"))
        events.append(
            {
                "id": str(r["id"]),
                "title": title,
                "start": r["date"],
                "backgroundColor": color,
                "borderColor": color,
                "textColor": "#ffffff",
                "extendedProps": {
                    "ticker":       r["ticker"],
                    "company":      company,
                    "quarter":      r.get("quarter"),
                    "fiscal_year":  r.get("fiscal_year"),
                    "eps_estimate": r.get("eps_estimate"),
                    "actual_eps":   r.get("actual_eps"),
                    "industry":     r.get("industry"),
                    "rank":         r.get("rank"),
                    "source":       r.get("source"),
                },
            }
        )

    return events


@app.get("/api/industries")
def list_industries():
    """Distinct industries for the filter dropdown."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT DISTINCT industry FROM companies WHERE industry IS NOT NULL ORDER BY industry"
        ).fetchall()
    return [r["industry"] for r in rows]


# ---------------------------------------------------------------------------
# Static files (local dev only — Vercel CDN handles this in production)
# ---------------------------------------------------------------------------

_public_dir = Path(__file__).parent.parent / "public"
if _public_dir.exists():
    from fastapi.staticfiles import StaticFiles
    app.mount("/", StaticFiles(directory=str(_public_dir), html=True), name="static")

# Vercel serverless entry-point
handler = Mangum(app, lifespan="off")
