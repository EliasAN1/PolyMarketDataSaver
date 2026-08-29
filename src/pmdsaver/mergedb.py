"""Merge a `pmdsaver.db` collected on another PC into this PC's database.

Windows are keyed by `slug`, which is derived purely from the fixed UTC 5-minute
grid (e.g. ``btc-updown-5m-1787410200``). Two independent collectors always
produce the *same* slug for the same wall-clock window, so windows -- and every
tick table that references `window_id` -- can be merged safely by matching on
slug and remapping the foreign key, instead of concatenating raw `id` values
(which collide across databases).

Usage (run on the PC that should end up with the combined dataset):

    python -m pmdsaver.mergedb path\\to\\other-pc\\pmdsaver.db

Before copying a database off another PC, stop its collector (Ctrl+C, for a
clean shutdown) and copy the *whole* data folder -- `pmdsaver.db` plus any
`pmdsaver.db-wal` / `pmdsaver.db-shm` next to it. SQLite keeps the most recent
writes in the `-wal` file until checkpointed, so copying `pmdsaver.db` alone
right after a run can silently drop the last stretch of data.
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from pmdsaver.db import SCHEMA, WINDOW_EXTRA_COLUMNS, ensure_window_columns
from pmdsaver.runtime import data_dir

WINDOW_COLUMNS = (
    "slug",
    "window_start",
    "window_end",
    "condition_id",
    "up_token_id",
    "down_token_id",
    "price_to_beat_rtds",
    "price_to_beat_gamma",
    "price_to_beat_source",
    "final_price",
    "outcome",
    "outcome_source",
    "created_at_ms",
)
WINDOW_FILL_COLUMNS = (
    "condition_id",
    "price_to_beat_rtds",
    "price_to_beat_gamma",
    "price_to_beat_source",
    "final_price",
    "outcome",
    "outcome_source",
)

ODDS_TICK_COLUMNS = (
    "window_id",
    "recv_ts_ms",
    "exchange_ts_ms",
    "event_type",
    "up_bid",
    "up_ask",
    "up_mid",
    "down_bid",
    "down_ask",
    "down_mid",
    "last_trade_token",
    "last_trade_price",
    "last_trade_size",
    "last_trade_side",
)
PRICE_TICK_COLUMNS = ("recv_ts_ms", "exchange_ts_ms", "source", "price", "size", "window_id")
TWAP_TICK_COLUMNS = ("recv_ts_ms", "exchange_ts_ms", "symbol", "value", "window_id")
CANDLE_VOLUME_COLUMNS = (
    "recv_ts_ms",
    "source",
    "open_time_ms",
    "base_volume",
    "quote_volume",
    "is_closed",
    "window_id",
)


@dataclass(slots=True)
class MergeStats:
    windows_inserted: int = 0
    windows_updated: int = 0
    windows_unchanged: int = 0
    tick_counts: dict[str, int] = field(default_factory=dict)


def _ensure_source_columns(conn: sqlite3.Connection) -> None:
    """Add any missing `windows` columns to the attached `src` schema too."""
    columns = {row[1] for row in conn.execute("PRAGMA src.table_info(windows)")}
    for name in WINDOW_EXTRA_COLUMNS:
        if name not in columns:
            conn.execute(f"ALTER TABLE src.windows ADD COLUMN {name} TEXT")


def checkpoint(path: Path) -> None:
    """Fold the WAL file into the main db file so only one file needs copying."""
    conn = sqlite3.connect(path)
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.commit()
    finally:
        conn.close()


def _merge_windows(conn: sqlite3.Connection, stats: MergeStats) -> dict[int, int]:
    id_map: dict[int, int] = {}
    src_rows = conn.execute(
        f"SELECT id, {', '.join(WINDOW_COLUMNS)} FROM src.windows ORDER BY window_start"
    ).fetchall()
    for row in src_rows:
        old_id = row[0]
        values = dict(zip(WINDOW_COLUMNS, row[1:]))
        existing = conn.execute(
            "SELECT id, " + ", ".join(WINDOW_FILL_COLUMNS) + " FROM main.windows WHERE slug = ?",
            (values["slug"],),
        ).fetchone()
        if existing is None:
            cur = conn.execute(
                f"INSERT INTO main.windows ({', '.join(WINDOW_COLUMNS)}) "
                f"VALUES ({', '.join('?' for _ in WINDOW_COLUMNS)})",
                tuple(values[c] for c in WINDOW_COLUMNS),
            )
            id_map[old_id] = cur.lastrowid
            stats.windows_inserted += 1
            continue

        existing_id = existing[0]
        id_map[old_id] = existing_id
        fills_gap = any(
            existing[i + 1] is None and values[col] is not None
            for i, col in enumerate(WINDOW_FILL_COLUMNS)
        )
        if not fills_gap:
            stats.windows_unchanged += 1
            continue
        set_clauses = [f"{col} = COALESCE(main.windows.{col}, ?)" for col in WINDOW_FILL_COLUMNS]
        params: list[object] = [values[col] for col in WINDOW_FILL_COLUMNS]
        params.append(existing_id)
        conn.execute(
            f"UPDATE main.windows SET {', '.join(set_clauses)} WHERE id = ?",
            params,
        )
        stats.windows_updated += 1
    return id_map


def _bulk_copy_ticks(
    conn: sqlite3.Connection,
    table: str,
    columns: tuple[str, ...],
    stats: MergeStats,
) -> None:
    select_cols = ", ".join(
        "map.new_id" if col == "window_id" else f"s.{col}" for col in columns
    )
    join = "LEFT JOIN temp.window_id_map AS map ON map.old_id = s.window_id"
    before = conn.execute(f"SELECT COUNT(*) FROM main.{table}").fetchone()[0]
    conn.execute(
        f"""
        INSERT INTO main.{table} ({', '.join(columns)})
        SELECT {select_cols}
        FROM src.{table} AS s
        {join}
        """
    )
    after = conn.execute(f"SELECT COUNT(*) FROM main.{table}").fetchone()[0]
    stats.tick_counts[table] = after - before


def _merge_candle_volume(conn: sqlite3.Connection, stats: MergeStats) -> None:
    rows = conn.execute(
        f"""
        SELECT {', '.join('s.' + c if c != 'window_id' else 'map.new_id' for c in CANDLE_VOLUME_COLUMNS)}
        FROM src.candle_volume AS s
        LEFT JOIN temp.window_id_map AS map ON map.old_id = s.window_id
        """
    ).fetchall()
    inserted = 0
    for row in rows:
        values = dict(zip(CANDLE_VOLUME_COLUMNS, row))
        cur = conn.execute(
            """
            INSERT INTO main.candle_volume
                (recv_ts_ms, source, open_time_ms, base_volume, quote_volume, is_closed, window_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source, open_time_ms) DO UPDATE SET
                recv_ts_ms = excluded.recv_ts_ms,
                base_volume = excluded.base_volume,
                quote_volume = excluded.quote_volume,
                is_closed = excluded.is_closed,
                window_id = COALESCE(main.candle_volume.window_id, excluded.window_id)
            WHERE excluded.recv_ts_ms > main.candle_volume.recv_ts_ms
            """,
            tuple(values[c] for c in CANDLE_VOLUME_COLUMNS),
        )
        inserted += cur.rowcount
    stats.tick_counts["candle_volume"] = inserted


def merge(target_path: Path, source_path: Path, *, backup: bool = True) -> MergeStats:
    target_path = Path(target_path).resolve()
    source_path = Path(source_path).resolve()
    if not source_path.exists():
        raise FileNotFoundError(f"Source database not found: {source_path}")
    if target_path == source_path:
        raise ValueError("Source and target resolve to the same file")

    target_path.parent.mkdir(parents=True, exist_ok=True)
    is_new_target = not target_path.exists()
    if not is_new_target and backup:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        backup_path = target_path.with_name(f"{target_path.stem}.bak-{stamp}{target_path.suffix}")
        shutil.copy2(target_path, backup_path)

    conn = sqlite3.connect(target_path)
    conn.isolation_level = None  # manual BEGIN/COMMIT below, no implicit transactions
    try:
        conn.executescript(SCHEMA)  # idempotent CREATE TABLE IF NOT EXISTS, covers a brand-new target
        ensure_window_columns(conn)
        conn.execute("ATTACH DATABASE ? AS src", (str(source_path),))
        _ensure_source_columns(conn)

        stats = MergeStats()
        conn.execute("BEGIN")
        try:
            id_map = _merge_windows(conn, stats)
            conn.execute("CREATE TEMP TABLE window_id_map (old_id INTEGER PRIMARY KEY, new_id INTEGER NOT NULL)")
            conn.executemany(
                "INSERT INTO window_id_map (old_id, new_id) VALUES (?, ?)",
                list(id_map.items()),
            )
            _bulk_copy_ticks(conn, "odds_ticks", ODDS_TICK_COLUMNS, stats)
            _bulk_copy_ticks(conn, "price_ticks", PRICE_TICK_COLUMNS, stats)
            _bulk_copy_ticks(conn, "twap_ticks", TWAP_TICK_COLUMNS, stats)
            _merge_candle_volume(conn, stats)
            conn.execute("DROP TABLE window_id_map")
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        return stats
    finally:
        try:
            conn.execute("DETACH DATABASE src")
        except sqlite3.OperationalError:
            pass
        conn.close()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Merge another PC's pmdsaver.db into this PC's database (matched by window slug).",
    )
    parser.add_argument("source", type=Path, help="Path to the other PC's pmdsaver.db (or a copy of it)")
    parser.add_argument(
        "--target",
        type=Path,
        default=None,
        help="Database to merge into (default: DATA_DIR/pmdsaver.db, same as the app uses)",
    )
    parser.add_argument("--no-backup", action="store_true", help="Skip backing up the target db before merging")
    parser.add_argument(
        "--checkpoint-source",
        action="store_true",
        help="Fold the source db's WAL file into it first (run this on the source PC before copying, if it still has a -wal file next to it)",
    )
    args = parser.parse_args(argv)

    if args.checkpoint_source:
        checkpoint(args.source)
        print(f"Checkpointed {args.source}")

    target = args.target or (data_dir() / "pmdsaver.db")
    print(f"Merging {args.source} -> {target}")
    stats = merge(target, args.source, backup=not args.no_backup)

    print(f"Windows: +{stats.windows_inserted} new, {stats.windows_updated} filled in gaps, {stats.windows_unchanged} already identical")
    for table, count in stats.tick_counts.items():
        print(f"  {table}: +{count} rows")
    print("Done.")


if __name__ == "__main__":
    main(sys.argv[1:])
