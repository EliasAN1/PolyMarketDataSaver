"""Backfill Polymarket-verified outcomes onto existing windows.

The live collector used to wait only 120s (in memory) for Gamma ``finalPrice``
and then infer UP/DOWN by comparing floats. Most historical rows in this
database never got an outcome at all, so backtests guessed the winner from
TWAP/spot ticks. This script asks Gamma for each window's resolved
``outcomePrices`` and writes the authoritative side.

Usage:

    python -m pmdsaver.backfill_outcomes
    python -m pmdsaver.backfill_outcomes --limit 20   # dry-run a slice
    pmdsaver.exe backfill-outcomes

Rate-limited to stay polite to gamma-api.polymarket.com.
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
import time
from pathlib import Path

import httpx

from pmdsaver.clock import window_from_slug
from pmdsaver.db import ensure_window_columns
from pmdsaver.gamma import GAMMA_BASE, extract_final_price, extract_price_to_beat, extract_resolved_outcome
from pmdsaver.runtime import data_dir

SLEEP_S = 0.12


def _backup(path: Path) -> Path:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = path.with_name(f"{path.stem}.bak-outcomes-{stamp}{path.suffix}")
    shutil.copy2(path, backup)
    return backup


def _update_lab_cache(slug: str, outcome: str, final_price: str | None) -> None:
    cache = data_dir() / "lab_cache.db"
    if not cache.exists():
        return
    conn = sqlite3.connect(cache)
    try:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(tapes)")}
        if "outcome" not in cols:
            return
        if "ptb" in cols and final_price:
            conn.execute(
                "UPDATE tapes SET outcome = ? WHERE slug = ?",
                (outcome, slug),
            )
        else:
            conn.execute("UPDATE tapes SET outcome = ? WHERE slug = ?", (outcome, slug))
        conn.commit()
    finally:
        conn.close()


def backfill(
    db_path: Path,
    *,
    limit: int | None = None,
    sleep_s: float = SLEEP_S,
    backup: bool = True,
) -> dict[str, int]:
    if not db_path.exists():
        raise FileNotFoundError(f"Database not found: {db_path}")
    if backup:
        dest = _backup(db_path)
        print(f"Backup: {dest}")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    ensure_window_columns(conn)
    now = int(time.time())
    rows = conn.execute(
        """
        SELECT id, slug FROM windows
        WHERE window_end <= ?
          AND (outcome IS NULL OR outcome = '' OR outcome_source IS NULL OR outcome_source != 'polymarket')
        ORDER BY window_end ASC
        """,
        (now,),
    ).fetchall()
    if limit is not None:
        rows = rows[:limit]
    stats = {"ok": 0, "skip": 0, "error": 0, "total": len(rows)}
    print(f"Windows to check: {len(rows)}")

    with httpx.Client(base_url=GAMMA_BASE, timeout=20.0) as client:
        for i, row in enumerate(rows, start=1):
            slug = row["slug"]
            window = window_from_slug(slug)
            if window is None:
                stats["skip"] += 1
                print(f"[{i}/{len(rows)}] skip (bad slug) {slug}")
                continue
            try:
                response = client.get(f"/events/slug/{slug}")
                if response.status_code == 404:
                    stats["skip"] += 1
                    print(f"[{i}/{len(rows)}] skip (404) {slug}")
                    continue
                response.raise_for_status()
                event = response.json()
            except Exception as exc:  # noqa: BLE001 - keep going through the rest of the windows
                stats["error"] += 1
                print(f"[{i}/{len(rows)}] error {slug}: {exc}")
                time.sleep(sleep_s)
                continue

            outcome = extract_resolved_outcome(event) if isinstance(event, dict) else None
            final = extract_final_price(event) if isinstance(event, dict) else None
            ptb = extract_price_to_beat(event) if isinstance(event, dict) else None
            if outcome is None:
                stats["skip"] += 1
                print(f"[{i}/{len(rows)}] unresolved {slug}")
            else:
                conn.execute(
                    """
                    UPDATE windows SET
                        outcome = ?,
                        outcome_source = 'polymarket',
                        final_price = COALESCE(?, final_price),
                        price_to_beat_gamma = COALESCE(?, price_to_beat_gamma)
                    WHERE slug = ?
                    """,
                    (outcome, final, ptb, slug),
                )
                conn.commit()
                _update_lab_cache(slug, outcome, final)
                stats["ok"] += 1
                print(f"[{i}/{len(rows)}] {slug} -> {outcome.upper()} final={final or '-'}")
            time.sleep(sleep_s)

    conn.close()
    return stats


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Backfill Polymarket-verified window outcomes from Gamma.")
    parser.add_argument("--db", type=Path, default=None, help="Database path (default: DATA_DIR/pmdsaver.db)")
    parser.add_argument("--limit", type=int, default=None, help="Max windows to process (for a test run)")
    parser.add_argument("--sleep", type=float, default=SLEEP_S, help="Seconds between Gamma requests")
    parser.add_argument("--no-backup", action="store_true")
    args = parser.parse_args(argv)
    target = args.db or (data_dir() / "pmdsaver.db")
    stats = backfill(target, limit=args.limit, sleep_s=args.sleep, backup=not args.no_backup)
    print(
        f"Done. verified={stats['ok']} unresolved={stats['skip']} errors={stats['error']} of {stats['total']}"
    )


if __name__ == "__main__":
    main(sys.argv[1:])
