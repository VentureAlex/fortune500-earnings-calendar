#!/usr/bin/env python3
"""
Refresh the Fortune 500 company list from a live source.

Sources tried in order:
  1. Public GitHub CSV (datasets/fortune500) — stable, no auth required
  2. 50pros.com table scrape — fallback

After fetching, companies no longer in the list are purged along with their
earnings records (via ON DELETE CASCADE).

Run: python scripts/update_companies.py
Schedule: monthly via GitHub Actions or cron (see .github/workflows/).

Scaling note: point SOURCE_URL at your own curated CSV in an S3 bucket or
              a private data API to remove the public-scrape dependency.
"""

import sys
import os
import csv
import io
import logging
import time

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
logger = logging.getLogger(__name__)

# Public GitHub CSV maintained by the community
GITHUB_CSV_URL = (
    "https://raw.githubusercontent.com/datasets/fortune500/main/data/fortune500.csv"
)
FIFTY_PROS_URL = "https://www.50pros.com/fortune500"


def fetch_from_github() -> list[dict]:
    """Try to pull from the datasets/fortune500 GitHub CSV."""
    logger.info("Fetching Fortune 500 list from GitHub CSV…")
    resp = requests.get(GITHUB_CSV_URL, timeout=15)
    resp.raise_for_status()
    reader = csv.DictReader(io.StringIO(resp.text))
    rows = list(reader)
    logger.info("Got %d rows from GitHub CSV", len(rows))
    return rows


def fetch_from_50pros() -> list[dict]:
    """Scrape 50pros.com as a fallback."""
    logger.info("Scraping 50pros.com Fortune 500 table…")
    headers = {"User-Agent": "Mozilla/5.0 (Fortune500EarningsCalendar/1.0)"}
    resp = requests.get(FIFTY_PROS_URL, headers=headers, timeout=20)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "lxml")
    table = soup.find("table")
    if not table:
        raise ValueError("Could not find table on 50pros.com")

    headers_row = [th.get_text(strip=True).lower() for th in table.find_all("th")]
    rows = []
    for tr in table.find_all("tr")[1:]:
        cells = [td.get_text(strip=True) for td in tr.find_all("td")]
        if len(cells) >= 2:
            row = dict(zip(headers_row, cells))
            rows.append(row)

    logger.info("Got %d rows from 50pros.com scrape", len(rows))
    return rows


def normalize_row(row: dict) -> dict | None:
    """Map various CSV column names to our schema fields."""
    def get(*keys):
        for k in keys:
            v = row.get(k) or row.get(k.lower()) or row.get(k.upper())
            if v:
                return str(v).strip()
        return None

    ticker = get("ticker", "symbol", "stock")
    name = get("company", "name", "company name")
    rank_raw = get("rank", "fortune rank", "#")
    industry = get("industry", "sector", "industry sector")

    if not ticker or not name:
        return None

    try:
        rank = int(str(rank_raw).replace(",", "")) if rank_raw else None
    except ValueError:
        rank = None

    return {"ticker": ticker.upper(), "name": name, "rank": rank, "industry": industry}


def update_companies():
    from api.database import init_db, get_db

    init_db()

    # Try GitHub first, fall back to scrape
    raw_rows: list[dict] = []
    for attempt in (fetch_from_github, fetch_from_50pros):
        try:
            raw_rows = attempt()
            if raw_rows:
                break
        except Exception as exc:
            logger.warning("Source failed: %s", exc)
            time.sleep(2)

    if not raw_rows:
        logger.error("All sources failed. Aborting update.")
        sys.exit(1)

    companies = [normalize_row(r) for r in raw_rows]
    companies = [c for c in companies if c]

    new_tickers = {c["ticker"] for c in companies}
    logger.info("Parsed %d valid companies", len(new_tickers))

    with get_db() as conn:
        # Purge companies (+ their earnings via CASCADE) no longer in list
        existing = {r[0] for r in conn.execute("SELECT ticker FROM companies").fetchall()}
        removed = existing - new_tickers
        if removed:
            placeholders = ",".join("?" * len(removed))
            conn.execute(f"DELETE FROM companies WHERE ticker IN ({placeholders})", list(removed))
            logger.info("Purged %d companies no longer in Fortune 500: %s", len(removed), removed)

        for c in companies:
            conn.execute(
                """INSERT INTO companies (name, ticker, rank, industry)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(ticker) DO UPDATE SET
                     name     = excluded.name,
                     rank     = excluded.rank,
                     industry = excluded.industry,
                     last_updated = datetime('now')""",
                (c["name"], c["ticker"], c["rank"], c["industry"]),
            )

    logger.info("Company list updated — %d total companies", len(companies))


if __name__ == "__main__":
    update_companies()
