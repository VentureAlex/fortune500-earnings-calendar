# Fortune 500 Earnings Calendar

A responsive single-page web app displaying earnings announcement dates for Fortune 500 companies, built on FastAPI + SQLite + FullCalendar.js.

## Live Demo

Deployed at: **https://fortune500-earnings.vercel.app** *(after your first Vercel deploy)*

---

## Features

- Interactive month / week / list calendar views (FullCalendar 6)
- Filter by year, fiscal quarter, industry, and ticker symbol
- Color-coded events by industry with a legend
- Event detail modal with EPS estimates, Yahoo Finance link
- Dark / light mode (persisted in localStorage)
- Mobile-responsive with collapsible sidebar
- Auto-seeds database with synthetic data for 65 Fortune 500 companies on first run

---

## Architecture

```
.
├── public/
│   └── index.html          # Single-page frontend (Tailwind + FullCalendar CDN)
├── api/
│   ├── index.py            # FastAPI app — serves /api/* routes
│   └── database.py         # SQLite helpers, schema, auto-seed logic
├── scripts/
│   ├── seed_data.py        # Seed DB from fortune500_seed.csv
│   ├── update_companies.py # Refresh Fortune 500 list from live source
│   └── fetch_earnings.py   # Fetch real earnings (FMP / API-Ninjas / Yahoo)
├── data/
│   └── fortune500_seed.csv # 65-company seed dataset
├── run.py                  # Local dev server (uvicorn)
├── vercel.json             # Vercel routing config
└── requirements.txt
```

**Data flow:**
1. On startup, `database.py` creates the SQLite schema and seeds it from `fortune500_seed.csv` if the DB is empty.
2. The frontend fetches `/api/earnings?start=…&end=…&[filters]` on every calendar navigation.
3. Scripts in `scripts/` can be run independently (or scheduled) to pull live data.

---

## Quick Start

### Prerequisites

- Python 3.11+
- pip

### 1. Clone & install

```bash
git clone https://github.com/VentureAlex/fortune500-earnings-calendar.git
cd fortune500-earnings-calendar
pip install -r requirements.txt
```

### 2. Configure (optional — seed data works without any API keys)

```bash
cp .env.example .env
# Edit .env and add your API keys if you want real earnings data
```

### 3. Run

```bash
python run.py
```

Open **http://localhost:8000** — the DB is auto-seeded on first launch.

---

## Loading Real Earnings Data

The app ships with synthetic earnings dates (generated from industry timing patterns). To pull real data:

### Option A — Financial Modeling Prep (250 req/day free)

1. Get a free key at https://financialmodelingprep.com/developer
2. Add `FMP_API_KEY=<key>` to `.env`
3. Run:
   ```bash
   python scripts/fetch_earnings.py --source fmp
   ```

### Option B — API-Ninjas (10,000 req/month free)

1. Get a free key at https://api-ninjas.com
2. Add `API_NINJAS_KEY=<key>` to `.env`
3. Run:
   ```bash
   python scripts/fetch_earnings.py --source ninjas
   ```

### Option C — Yahoo Finance (no key required)

```bash
python scripts/fetch_earnings.py --source yahoo
```
*Rate-limited to 1 request/3s automatically.*

### Refreshing the Fortune 500 list

```bash
python scripts/update_companies.py
```

---

## Deploy to Vercel

### One-click via GitHub integration

1. Push this repo to GitHub (already done if you're reading this on GitHub)
2. Go to https://vercel.com/new → Import your GitHub repo
3. Vercel auto-detects the Python + static config from `vercel.json`
4. Click **Deploy**

### Via Vercel CLI

```bash
npm i -g vercel
vercel --prod
```

> **Note on persistence:** Vercel serverless functions are stateless. The SQLite DB lives in `/tmp` and is re-seeded (from the CSV bundled in the repo) on each cold start. For persistent real data:
> - Connect to a hosted Postgres (Neon / Supabase / Railway — all have free tiers)
> - Or run the fetch scripts locally and commit the generated DB (not recommended for large datasets)

---

## Scheduled Data Refresh (GitHub Actions)

Add `.github/workflows/refresh.yml`:

```yaml
name: Refresh Earnings Data
on:
  schedule:
    - cron: '0 6 * * 1'  # Every Monday at 6 AM UTC

jobs:
  refresh:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.11' }
      - run: pip install -r requirements.txt
      - run: python scripts/update_companies.py
      - run: python scripts/fetch_earnings.py
        env:
          FMP_API_KEY: ${{ secrets.FMP_API_KEY }}
```

---

## Scaling Notes

| Concern | Current (POC) | Production path |
|---------|--------------|-----------------|
| Database | SQLite (file-based) | Postgres (Neon, Supabase, Railway) |
| Data source | Synthetic seed + free APIs | Paid FMP / Polygon.io tier |
| Scheduling | Manual scripts | GitHub Actions / Railway cron |
| Auth | None | Clerk / Auth0 for private deployments |
| Frontend | CDN-loaded FullCalendar | Vite + React for SSR/better perf |

---

## License

MIT
