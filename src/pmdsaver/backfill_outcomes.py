"""Resolve window winners the same way the live trader does.

Order: CLOB book around close (one side >= 0.95, the other <= 0.10), then
Gamma. Writes ``windows.outcome`` / ``outcome_source`` and patches the Lab tape.

Usage:

    python -m pmdsaver.backfill_outcomes --db path\\to\\pmdsaver.db --no-backup
    pmdsaver.exe backfill-outcomes
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
from pmdsaver.outcome import infer_outcome_from_odds_ticks
from pmdsaver.runtime import data_dir

SLEEP_S = 0.12


def _backup(path: Path) -> Path:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = path.with_name(f"{path.stem}.bak-outcomes-{stamp}{path.suffix}")
    shutil.copy2(path, backup)
    return backup


def _update_lab_cache(slug: str, outcome: str) -> None:
    from pmdsaver.backtest.tape import CACHE_FILE_NAME

    cache = data_dir() / CACHE_FILE_NAME
    if not cache.exists():
        return
    conn = sqlite3.connect(cache)
    try:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(tapes)")}
        if "outcome" not in cols:
            return
        conn.execute("UPDATE tapes SET outcome = ? WHERE slug = ?", (outcome, slug))
        conn.commit()
    finally:
        conn.close()


def _sync_lab_cache_from_windows(conn: sqlite3.Connection) -> int:
    """Copy every official/CLOB winner onto the Lab tape so PnL matches."""
    from pmdsaver.backtest.tape import CACHE_FILE_NAME

    cache = data_dir() / CACHE_FILE_NAME
    if not cache.exists():
        return 0
    rows = conn.execute(
        """
        SELECT slug, outcome FROM windows
        WHERE outcome IN ('up', 'down')
          AND outcome_source IN ('polymarket', 'gamma', 'clob_live', 'clob_rest', 'clob')
        """
    ).fetchall()
    cache_conn = sqlite3.connect(cache)
    try:
        cols = {row[1] for row in cache_conn.execute("PRAGMA table_info(tapes)")}
        if "outcome" not in cols:
            return 0
        updated = 0
        for slug, outcome in rows:
            cur = cache_conn.execute(
                "UPDATE tapes SET outcome = ? WHERE slug = ? AND (outcome IS NULL OR outcome != ?)",
                (outcome, slug, outcome),
            )
            updated += cur.rowcount
        cache_conn.commit()
        return updated
    finally:
        cache_conn.close()


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
        SELECT id, slug, window_end FROM windows
        WHERE window_end <= ?
          AND (
            outcome IS NULL OR outcome = ''
            OR outcome_source IS NULL
            OR outcome_source NOT IN ('polymarket', 'gamma', 'clob_live', 'clob_rest', 'clob')
          )
        ORDER BY window_end ASC
        """,
        (now,),
    ).fetchall()
    if limit is not None:
        rows = rows[:limit]
    stats = {"ok": 0, "skip": 0, "error": 0, "total": len(rows), "cache": 0}
    print(f"Windows to check: {len(rows)}")

    with httpx.Client(base_url=GAMMA_BASE, timeout=20.0) as client:
        for i, row in enumerate(rows, start=1):
            slug = row["slug"]
            window = window_from_slug(slug)
            if window is None:
                stats["skip"] += 1
                print(f"[{i}/{len(rows)}] skip (bad slug) {slug}")
                continue

            outcome = infer_outcome_from_odds_ticks(
                conn, window_id=int(row["id"]), window_end=int(row["window_end"])
            )
            source = "clob" if outcome else None
            final = None
            ptb = None

            if outcome is None:
                try:
                    response = client.get(f"/events/slug/{slug}")
                    if response.status_code == 404:
                        stats["skip"] += 1
                        print(f"[{i}/{len(rows)}] skip (404) {slug}")
                        time.sleep(sleep_s)
                        continue
                    response.raise_for_status()
                    event = response.json()
                except Exception as exc:  # noqa: BLE001
                    stats["error"] += 1
                    print(f"[{i}/{len(rows)}] error {slug}: {exc}")
                    time.sleep(sleep_s)
                    continue
                if isinstance(event, dict):
                    outcome = extract_resolved_outcome(event)
                    final = extract_final_price(event)
                    ptb = extract_price_to_beat(event)
                    if outcome is not None:
                        source = "gamma"
                time.sleep(sleep_s)

            if outcome is None:
                stats["skip"] += 1
                print(f"[{i}/{len(rows)}] unresolved {slug}")
                continue

            conn.execute(
                """
                UPDATE windows SET
                    outcome = ?,
                    outcome_source = ?,
                    final_price = COALESCE(?, final_price),
                    price_to_beat_gamma = COALESCE(?, price_to_beat_gamma)
                WHERE slug = ?
                """,
                (outcome, source, final, ptb, slug),
            )
            conn.commit()
            _update_lab_cache(slug, outcome)
            stats["ok"] += 1
            print(f"[{i}/{len(rows)}] {slug} -> {outcome.upper()} ({source})")

    stats["cache"] = _sync_lab_cache_from_windows(conn)
    conn.close()
    return stats


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Resolve window outcomes the same way the live trader does.")
    parser.add_argument("--db", type=Path, default=None, help="Database path (default: DATA_DIR/pmdsaver.db)")
    parser.add_argument("--limit", type=int, default=None, help="Max windows to process (for a test run)")
    parser.add_argument("--sleep", type=float, default=SLEEP_S, help="Seconds between Gamma requests")
    parser.add_argument("--no-backup", action="store_true")
    args = parser.parse_args(argv)
    target = args.db or (data_dir() / "pmdsaver.db")
    stats = backfill(target, limit=args.limit, sleep_s=args.sleep, backup=not args.no_backup)
    print(
        f"Done. resolved={stats['ok']} unresolved={stats['skip']} errors={stats['error']} "
        f"of {stats['total']}; lab cache rows updated={stats['cache']}"
    )


if __name__ == "__main__":
    main(sys.argv[1:])
