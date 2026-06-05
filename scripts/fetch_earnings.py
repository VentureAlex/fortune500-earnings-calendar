#!/usr/bin/env python3
"""
Fetch real earnings dates for all Fortune 500 companies.

Supported sources (tried in order):
  1. Financial Modeling Prep (FMP) free tier — 250 req/day
     Set FMP_API_KEY in .env
  2. API-Ninjas Earnings Calendar — 10,000 req/month free
     Set API_NINJAS_KEY in .env
  3. Yahoo Finance scrape — no key required, politely rate-limited

Run: python scripts/fetch_earnings.py [--days 365] [--source fmp|ninjas|yahoo]
Schedule: weekly via GitHub Actions or cron.

Scaling note: replace the free APIs with a paid tier (FMP paid, Polygon.io,
              Intrinio) and swap SQLite → Postgres for concurrent writers.
"""

import sys
import os
import logging
import time
import argparse
from datetime import date, timedelta

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
logger = logging.getLogger(__name__)

FMP_KEY = os.environ.get("FMP_API_KEY", "")
NINJAS_KEY = os.environ.get("API_NINJAS_KEY", "")


# ---------------------------------------------------------------------------
# FMP source
# ---------------------------------------------------------------------------

def fetch_fmp_earnings(start: str, end: str) -> list[dict]:
    """
    Fetch the earnings calendar from Financial Modeling Prep.
    Returns a flat list of {ticker, date, eps_estimate, actual_eps} dicts.
    """
    if not FMP_KEY:
        raise EnvironmentError("FMP_API_KEY not set")

    url = "https://financialmodelingprep.com/api/v3/earning_calendar"
    params = {"from": start, "to": end, "apikey": FMP_KEY}
    resp = requests.get(url, params=params, timeout=20)
    resp.raise_for_status()
    data = resp.json()

    if isinstance(data, dict) and data.get("Error Message"):
        raise ValueError(data["Error Message"])

    results = []
    for item in data:
        symbol = (item.get("symbol") or "").upper()
        if not symbol:
            continue
        results.append(
            {
                "ticker":       symbol,
                "date":         item.get("date", ""),
                "eps_estimate": item.get("epsEstimated"),
                "actual_eps":   item.get("eps"),
                "source":       "fmp",
            }
        )
    return results


# ---------------------------------------------------------------------------
# API-Ninjas source
# ---------------------------------------------------------------------------

def fetch_ninjas_earnings(ticker: str) -> list[dict]:
    """Fetch upcoming earnings for a single ticker from API-Ninjas."""
    if not NINJAS_KEY:
        raise EnvironmentError("API_NINJAS_KEY not set")

    url = "https://api.api-ninjas.com/v1/earningscalendar"
    headers = {"X-Api-Key": NINJAS_KEY}
    params = {"ticker": ticker}
    resp = requests.get(url, headers=headers, params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    results = []
    for item in data:
        results.append(
            {
                "ticker":       ticker,
                "date":         item.get("date", ""),
                "eps_estimate": item.get("eps_estimate"),
                "actual_eps":   item.get("eps_actual"),
                "source":       "api-ninjas",
            }
        )
    return results


# ---------------------------------------------------------------------------
# Yahoo Finance scrape (fallback, no API key needed)
# ---------------------------------------------------------------------------

def fetch_yahoo_earnings(ticker: str) -> list[dict]:
    """
    Scrape Yahoo Finance earnings page for a single ticker.
    Rate-limited to ~1 req/3s to be polite.
    """
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        raise ImportError("beautifulsoup4 required for Yahoo scrape")

    url = f"https://finance.yahoo.com/quote/{ticker}/financials/"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )
    }
    resp = requests.get(url, headers=headers, timeout=15)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "lxml")
    # Yahoo Finance structure changes often — this is a best-effort parse
    results = []
    for row in soup.select("tr"):
        cells = [td.get_text(strip=True) for td in row.select("td")]
        if len(cells) >= 2 and "earnings" in cells[0].lower():
            results.append(
                {
                    "ticker":       ticker,
                    "date":         cells[1] if len(cells) > 1 else "",
                    "eps_estimate": None,
                    "actual_eps":   None,
                    "source":       "yahoo",
                }
            )
    return results


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def upsert_earnings(rows: list[dict]):
    """Write fetched earnings into the DB, skipping rows with no date."""
    from api.database import get_db

    with get_db() as conn:
        for row in rows:
            if not row.get("date"):
                continue
            ticker = row["ticker"].upper()
            company = conn.execute(
                "SELECT id FROM companies WHERE ticker = ?", (ticker,)
            ).fetchone()
            if not company:
                continue

            conn.execute(
                """INSERT INTO earnings (company_id, ticker, date, eps_estimate, actual_eps, source)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(ticker, date) DO UPDATE SET
                     eps_estimate = COALESCE(excluded.eps_estimate, earnings.eps_estimate),
                     actual_eps   = COALESCE(excluded.actual_eps,   earnings.actual_eps),
                     source       = excluded.source,
                     last_fetched = datetime('now')""",
                (company[0], ticker, row["date"],
                 row.get("eps_estimate"), row.get("actual_eps"), row.get("source", "api")),
            )


def prune_old_earnings():
    """Remove earnings older than today (keep last 30 days for reference)."""
    from api.database import get_db
    cutoff = (date.today() - timedelta(days=30)).isoformat()
    with get_db() as conn:
        deleted = conn.execute(
            "DELETE FROM earnings WHERE date < ? AND actual_eps IS NULL", (cutoff,)
        ).rowcount
    logger.info("Pruned %d stale earnings records", deleted)


def main():
    parser = argparse.ArgumentParser(description="Fetch real earnings data")
    parser.add_argument("--days", type=int, default=365, help="Days ahead to fetch (default 365)")
    parser.add_argument(
        "--source",
        choices=["fmp", "ninjas", "yahoo", "auto"],
        default="auto",
        help="Data source (default: auto — tries fmp, then ninjas, then yahoo)",
    )
    parser.add_argument("--prune", action="store_true", default=True, help="Prune old records")
    args = parser.parse_args()

    from api.database import init_db, get_db

    init_db()

    start_str = date.today().isoformat()
    end_str = (date.today() + timedelta(days=args.days)).isoformat()

    if args.prune:
        prune_old_earnings()

    # ------------------------------------------------------------------
    # Strategy 1: FMP bulk calendar (one request for all tickers)
    # ------------------------------------------------------------------
    if args.source in ("fmp", "auto") and FMP_KEY:
        try:
            logger.info("Fetching via FMP (bulk calendar) %s → %s", start_str, end_str)
            rows = fetch_fmp_earnings(start_str, end_str)
            logger.info("FMP returned %d events", len(rows))

            # Filter to only our tracked tickers
            with get_db() as conn:
                tracked = {r[0] for r in conn.execute("SELECT ticker FROM companies").fetchall()}
            rows = [r for r in rows if r["ticker"] in tracked]
            logger.info("Matched %d events to tracked tickers", len(rows))

            upsert_earnings(rows)
            logger.info("FMP earnings saved.")
            return
        except Exception as exc:
            logger.warning("FMP fetch failed: %s", exc)

    # ------------------------------------------------------------------
    # Strategy 2 & 3: per-ticker (ninjas or yahoo)
    # ------------------------------------------------------------------
    from api.database import get_db

    with get_db() as conn:
        tickers = [r[0] for r in conn.execute("SELECT ticker FROM companies ORDER BY rank").fetchall()]

    fetch_fn = None
    if args.source == "ninjas" or (args.source == "auto" and NINJAS_KEY):
        fetch_fn = fetch_ninjas_earnings
        source_name = "API-Ninjas"
        delay = 0.3  # ~3 req/s, well within free tier
    else:
        fetch_fn = fetch_yahoo_earnings
        source_name = "Yahoo Finance"
        delay = 3.0  # polite scrape rate

    logger.info("Fetching per-ticker via %s for %d tickers…", source_name, len(tickers))
    total_saved = 0
    for i, ticker in enumerate(tickers, 1):
        try:
            rows = fetch_fn(ticker)
            upsert_earnings(rows)
            total_saved += len(rows)
            if i % 10 == 0:
                logger.info("  %d/%d tickers processed, %d events so far", i, len(tickers), total_saved)
        except Exception as exc:
            logger.warning("  %s failed for %s: %s", source_name, ticker, exc)
        time.sleep(delay)

    logger.info("Done. Saved %d earnings events.", total_saved)


if __name__ == "__main__":
    main()
