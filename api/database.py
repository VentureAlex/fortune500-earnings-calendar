import sqlite3
import os
import logging
import csv
import hashlib
from pathlib import Path
from contextlib import contextmanager
from datetime import date, timedelta

logger = logging.getLogger(__name__)

# Use /tmp on Vercel (serverless, read-only fs except /tmp), local path otherwise
IS_VERCEL = bool(os.environ.get("VERCEL"))
_default_db = "/tmp/earnings.db" if IS_VERCEL else str(Path(__file__).parent.parent / "earnings.db")
DATABASE_PATH = os.environ.get("DATABASE_PATH", _default_db)

SCHEMA = """
CREATE TABLE IF NOT EXISTS companies (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL,
    ticker      TEXT    UNIQUE NOT NULL,
    rank        INTEGER,
    industry    TEXT,
    revenue_billions REAL,
    last_updated TEXT   DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS earnings (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id   INTEGER REFERENCES companies(id) ON DELETE CASCADE,
    ticker       TEXT    NOT NULL,
    date         TEXT    NOT NULL,
    quarter      TEXT,
    fiscal_year  INTEGER,
    eps_estimate REAL,
    actual_eps   REAL,
    source       TEXT    DEFAULT 'seed',
    last_fetched TEXT    DEFAULT (datetime('now')),
    UNIQUE(ticker, date)
);

CREATE INDEX IF NOT EXISTS idx_earnings_date   ON earnings(date);
CREATE INDEX IF NOT EXISTS idx_earnings_ticker ON earnings(ticker);
CREATE INDEX IF NOT EXISTS idx_earnings_year   ON earnings(fiscal_year);
"""

SEED_CSV = Path(__file__).parent.parent / "data" / "fortune500_seed.csv"

# Industry-based timing: days offset from the quarter-end date when earnings are announced
_INDUSTRY_OFFSETS = {
    "Financials":              30,   # Banks report ~4-5 weeks after quarter end
    "Technology":              42,
    "Healthcare":              44,
    "Healthcare Distribution": 40,
    "Energy":                  38,
    "Retail":                  46,
    "Consumer Staples":        43,
    "Consumer Discretionary":  45,
    "Automotive":              42,
    "Telecommunications":      41,
    "Media & Telecom":         42,
    "Media & Entertainment":   43,
    "Industrial":              44,
    "Industrial & Agriculture": 47,
    "Industrial Conglomerate":  44,
    "Aerospace & Defense":     45,
    "Logistics":               46,
    "Real Estate":             50,
    "default":                 42,
}

# Calendar-year fiscal quarters (used for most companies)
_QUARTER_ENDS = {
    "Q1_2025": date(2025, 3, 31),
    "Q2_2025": date(2025, 6, 30),
    "Q3_2025": date(2025, 9, 30),
    "Q4_2025": date(2025, 12, 31),
    "Q1_2026": date(2026, 3, 31),
    "Q2_2026": date(2026, 6, 30),
}

_QUARTER_LABELS = {
    "Q1_2025": ("Q1", 2025),
    "Q2_2025": ("Q2", 2025),
    "Q3_2025": ("Q3", 2025),
    "Q4_2025": ("Q4", 2025),
    "Q1_2026": ("Q1", 2026),
    "Q2_2026": ("Q2", 2026),
}

# Representative EPS estimates by sector (purely illustrative)
_INDUSTRY_EPS = {
    "Technology":   (1.20, 8.50),
    "Financials":   (2.50, 5.00),
    "Healthcare":   (0.80, 4.50),
    "Energy":       (1.50, 6.00),
    "Retail":       (0.40, 3.50),
    "default":      (0.50, 3.00),
}


def _next_weekday(d: date) -> date:
    """Advance to Monday if d falls on a weekend."""
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def _company_hash_offset(ticker: str, spread: int = 10) -> int:
    """Deterministic per-company day spread so events don't all land on the same day."""
    h = int(hashlib.md5(ticker.encode()).hexdigest()[:4], 16)
    return h % (spread + 1)


def _make_earnings_for(ticker: str, industry: str) -> list[dict]:
    """Generate synthetic earnings dates for all tracked quarters."""
    base_offset = _INDUSTRY_OFFSETS.get(industry, _INDUSTRY_OFFSETS["default"])
    company_offset = _company_hash_offset(ticker)
    total_offset = base_offset + company_offset

    lo, hi = None, None
    for key, (lo_v, hi_v) in _INDUSTRY_EPS.items():
        if key.lower() in (industry or "").lower():
            lo, hi = lo_v, hi_v
            break
    if lo is None:
        lo, hi = _INDUSTRY_EPS["default"]

    eps_offset = (_company_hash_offset(ticker + "eps", spread=20) / 20) * (hi - lo) + lo

    rows = []
    for qkey, qend in _QUARTER_ENDS.items():
        announce = _next_weekday(qend + timedelta(days=total_offset))
        label, fy = _QUARTER_LABELS[qkey]
        rows.append({
            "ticker":       ticker,
            "date":         announce.isoformat(),
            "quarter":      label,
            "fiscal_year":  fy,
            "eps_estimate": round(eps_offset, 2),
            "source":       "seed",
        })
    return rows


@contextmanager
def get_db():
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with get_db() as conn:
        conn.executescript(SCHEMA)
    logger.info("Database initialized at %s", DATABASE_PATH)


def is_empty() -> bool:
    try:
        with get_db() as conn:
            return conn.execute("SELECT COUNT(*) FROM companies").fetchone()[0] == 0
    except Exception:
        return True


def seed_from_csv():
    """Load Fortune 500 companies + generate synthetic earnings from the seed CSV."""
    if not SEED_CSV.exists():
        logger.warning("Seed CSV not found at %s", SEED_CSV)
        return

    companies = []
    with open(SEED_CSV, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            companies.append(row)

    with get_db() as conn:
        for c in companies:
            conn.execute(
                """INSERT OR REPLACE INTO companies (name, ticker, rank, industry, revenue_billions)
                   VALUES (?, ?, ?, ?, ?)""",
                (c["name"], c["ticker"], int(c["rank"]), c["industry"],
                 float(c.get("revenue_billions") or 0)),
            )
        conn.commit()

        for c in companies:
            company_id = conn.execute(
                "SELECT id FROM companies WHERE ticker = ?", (c["ticker"],)
            ).fetchone()["id"]

            for row in _make_earnings_for(c["ticker"], c["industry"]):
                conn.execute(
                    """INSERT OR IGNORE INTO earnings
                       (company_id, ticker, date, quarter, fiscal_year, eps_estimate, source)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (company_id, row["ticker"], row["date"], row["quarter"],
                     row["fiscal_year"], row["eps_estimate"], row["source"]),
                )

    logger.info("Seeded %d companies with synthetic earnings", len(companies))


def ensure_seeded():
    """Initialize schema and seed if the DB is empty (called on every startup)."""
    init_db()
    if is_empty() or os.environ.get("FORCE_RESEED") == "true":
        logger.info("Seeding database from CSV…")
        seed_from_csv()
