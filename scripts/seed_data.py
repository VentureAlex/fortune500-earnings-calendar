#!/usr/bin/env python3
"""
Seed the database with Fortune 500 companies + synthetic earnings dates.
Run: python scripts/seed_data.py [--force]
"""

import sys
import os
import argparse

# Allow importing from project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()


def main():
    parser = argparse.ArgumentParser(description="Seed the earnings database")
    parser.add_argument("--force", action="store_true", help="Re-seed even if data exists")
    args = parser.parse_args()

    from api.database import init_db, is_empty, seed_from_csv

    init_db()

    if not is_empty() and not args.force:
        print("Database already has data. Use --force to re-seed.")
        return

    if args.force:
        # Wipe existing data so we get a clean seed
        from api.database import get_db
        with get_db() as conn:
            conn.execute("DELETE FROM earnings")
            conn.execute("DELETE FROM companies")
        print("Cleared existing data.")

    seed_from_csv()
    print("Seed complete.")

    # Summary
    from api.database import get_db
    with get_db() as conn:
        companies = conn.execute("SELECT COUNT(*) FROM companies").fetchone()[0]
        earnings = conn.execute("SELECT COUNT(*) FROM earnings").fetchone()[0]
    print(f"  {companies} companies, {earnings} earnings events")


if __name__ == "__main__":
    main()
