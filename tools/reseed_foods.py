#!/usr/bin/env python3
"""Reload the curated food list from data/foods_seed.csv.

Deletes everything in `foods` EXCEPT rows you added yourself (`source='custom'`),
then reloads the CSV as `curated`. Log history is unaffected — log_entries carry
their own computed calories/protein/fiber and only a soft food_id reference. New
food ids continue above the previous max so a stale food_id can't accidentally
resolve to a different food.

Usage:  venv/bin/python tools/reseed_foods.py [--yes]
A timestamped DB copy is written to backups/ first.
"""

import csv
import shutil
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "calorie.db"
CSV_PATH = ROOT / "data" / "foods_seed.csv"
BACKUPS = ROOT / "backups"


def _f(v):
    v = (v or "").strip()
    if v == "":
        return None
    try:
        return float(v)
    except ValueError:
        return None


def main():
    if not DB.exists():
        sys.exit(f"no db at {DB} — nothing to do (a fresh db seeds itself)")
    rows = list(csv.DictReader(CSV_PATH.open(newline="")))
    if not rows:
        sys.exit("seed csv is empty")

    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    before = conn.execute("SELECT COUNT(*) FROM foods").fetchone()[0]
    custom = conn.execute("SELECT COUNT(*) FROM foods WHERE source='custom'").fetchone()[0]
    logs = conn.execute("SELECT COUNT(*) FROM log_entries").fetchone()[0]
    print(f"db: {DB}")
    print(f"foods now: {before}  (keeping {custom} custom)   log_entries: {logs}")
    print(f"seed csv:  {len(rows)} rows")

    if "--yes" not in sys.argv:
        if input("reload curated foods from the csv? [y/N] ").strip().lower() != "y":
            sys.exit("aborted")

    BACKUPS.mkdir(exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dst = BACKUPS / f"calorie.db.{stamp}.pre-reseed.bak"
    shutil.copy2(DB, dst)
    print(f"backup: {dst}")

    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    with conn:
        # keep sqlite_sequence so new ids continue above the old max;
        # keep the user's own foods
        conn.execute("DELETE FROM foods WHERE source != 'custom'")
        for r in rows:
            name = (r.get("name") or "").strip()
            if not name:
                continue
            conn.execute(
                """INSERT INTO foods
                   (name, brand, source, serving_desc, serving_grams,
                    calories, protein_g, fiber_g, carbs_g, fat_g, created_at)
                   VALUES (?, ?, 'curated', ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    name,
                    (r.get("brand") or "").strip() or None,
                    (r.get("serving_desc") or "100 g").strip(),
                    _f(r.get("serving_grams")),
                    _f(r.get("calories")) or 0.0,
                    _f(r.get("protein_g")) or 0.0,
                    _f(r.get("fiber_g")) or 0.0,
                    _f(r.get("carbs_g")),
                    _f(r.get("fat_g")),
                    now,
                ),
            )
    conn.execute("VACUUM")
    after = conn.execute("SELECT COUNT(*) FROM foods").fetchone()[0]
    cur = conn.execute("SELECT COUNT(*) FROM foods WHERE source='curated'").fetchone()[0]
    conn.close()
    print(f"done: foods {before} -> {after}  ({cur} curated + {custom} custom)")


if __name__ == "__main__":
    main()
