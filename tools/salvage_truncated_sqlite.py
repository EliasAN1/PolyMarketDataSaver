"""Salvage a malformed pmdsaver SQLite file into a new consistent database.

See docs/recovering-truncated-sqlite.md. Work is written to a new file; the
source is opened read-only and is never modified.
"""
from __future__ import annotations

import argparse
import sqlite3
import struct
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from pmdsaver.db import SCHEMA  # noqa: E402

WINDOW_COLS = (
    "id",
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
ODDS_COLS = (
    "id",
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
PRICE_COLS = ("id", "recv_ts_ms", "exchange_ts_ms", "source", "price", "size", "window_id")
TWAP_COLS = ("id", "recv_ts_ms", "exchange_ts_ms", "symbol", "value", "window_id")
CANDLE_COLS = (
    "id",
    "recv_ts_ms",
    "source",
    "open_time_ms",
    "base_volume",
    "quote_volume",
    "is_closed",
    "window_id",
)

_SCHEMA_BODY = "\n".join(
    line for line in SCHEMA.splitlines() if not line.strip().upper().startswith("PRAGMA")
)
_STATEMENTS = [s.strip() for s in _SCHEMA_BODY.split(";") if s.strip()]
TABLES_SQL = ";\n".join(s for s in _STATEMENTS if s.upper().startswith("CREATE TABLE")) + ";"
INDEXES_SQL = (
    ";\n".join(s for s in _STATEMENTS if s.upper().startswith("CREATE INDEX") or s.upper().startswith("CREATE UNIQUE INDEX"))
    + ";"
)


def log(msg: str) -> None:
    print(msg, flush=True)


def header_info(path: Path) -> tuple[int, int, int]:
    size = path.stat().st_size
    with path.open("rb") as f:
        hdr = f.read(100)
    page_size = struct.unpack(">H", hdr[16:18])[0]
    if page_size == 1:
        page_size = 65536
    page_count = struct.unpack(">I", hdr[28:32])[0]
    return size, page_size, page_count


def is_malformed(exc: BaseException) -> bool:
    text = str(exc).lower()
    return isinstance(exc, sqlite3.DatabaseError) and (
        "malformed" in text or "corrupt" in text or "invalid page" in text
    )


def copy_full(conn: sqlite3.Connection, table: str, cols: tuple[str, ...]) -> int:
    col_list = ", ".join(cols)
    t0 = time.time()
    conn.execute(
        f"INSERT INTO main.{table} ({col_list}) SELECT {col_list} FROM src.{table}"
    )
    n = conn.execute("SELECT changes()").fetchone()[0]
    conn.commit()
    log(f"  {table}: copied {n:,} rows in {time.time() - t0:.1f}s")
    return int(n)


def copy_id_range(
    conn: sqlite3.Connection, table: str, cols: tuple[str, ...], lo: int, hi: int
) -> int:
    col_list = ", ".join(cols)
    conn.execute(
        f"INSERT INTO main.{table} ({col_list}) SELECT {col_list} FROM src.{table} "
        f"WHERE id > ? AND id <= ?",
        (lo, hi),
    )
    return int(conn.execute("SELECT changes()").fetchone()[0])


def copy_until_corrupt(
    conn: sqlite3.Connection,
    table: str,
    cols: tuple[str, ...],
    max_id: int,
    start_step: int = 1_000_000,
) -> int:
    lo = 0
    step = start_step
    last_good = 0
    t0 = time.time()
    copied = 0
    while lo < max_id:
        hi = min(lo + step, max_id)
        try:
            n = copy_id_range(conn, table, cols, lo, hi)
            conn.commit()
            copied += n
            last_good = hi
            elapsed = time.time() - t0
            log(f"  {table}: through id {hi:,} (+{n:,} rows, {copied:,} total, {elapsed:.0f}s)")
            lo = hi
            if step < start_step:
                step = min(start_step, step * 2)
        except sqlite3.DatabaseError as e:
            conn.rollback()
            if not is_malformed(e):
                raise
            log(f"  {table}: malformed in ({lo:,}, {hi:,}]: {e}")
            if hi <= lo + 1:
                log(f"  {table}: stopping; last good id {lo:,}")
                break
            last_ok = lo
            bad = hi
            while bad - last_ok > 1:
                mid = (last_ok + bad) // 2
                try:
                    n = copy_id_range(conn, table, cols, last_ok, mid)
                    conn.commit()
                    copied += n
                    last_ok = mid
                    log(f"  {table}: bisect ok through {mid:,} (+{n:,})")
                except sqlite3.DatabaseError as e2:
                    conn.rollback()
                    if not is_malformed(e2):
                        raise
                    bad = mid
                    log(f"  {table}: bisect fail ({last_ok:,}, {mid:,}]")
            last_good = last_ok
            log(f"  {table}: last good id {last_good:,}; first bad {bad:,}")
            break
    return last_good


def src_max_id(conn: sqlite3.Connection, table: str) -> int:
    seq = conn.execute(
        "SELECT seq FROM src.sqlite_sequence WHERE name=?", (table,)
    ).fetchone()
    return int(seq[0]) if seq else 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", nargs="?", default="data/pmdsaver.db")
    parser.add_argument("-o", "--output", default="data/pmdsaver.fixed.db")
    args = parser.parse_args()
    src = Path(args.source).resolve()
    dest = Path(args.output).resolve()
    if not src.is_file():
        log(f"missing source {src}")
        return 1
    if dest.exists():
        log(f"removing previous {dest}")
        dest.unlink()
        for extra in (Path(str(dest) + "-wal"), Path(str(dest) + "-shm")):
            if extra.exists():
                extra.unlink()

    size, page_size, page_count = header_info(src)
    pages_on_disk = size // page_size
    log(
        f"source {src} size={size:,} header_pages={page_count:,} "
        f"pages_on_disk={pages_on_disk:,}"
    )
    if pages_on_disk < page_count:
        log(
            "WARNING: truncated vs header. SQLite may still read some trees; "
            "missing tail pages cannot be reconstructed."
        )

    dest.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(dest)
    conn.execute("PRAGMA journal_mode=OFF")
    conn.execute("PRAGMA synchronous=OFF")
    conn.execute("PRAGMA temp_store=MEMORY")
    conn.execute("PRAGMA cache_size=-1000000")
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.executescript(TABLES_SQL)
    conn.commit()

    conn.execute(f"ATTACH DATABASE ? AS src", (str(src),))

    intact = [
        ("windows", WINDOW_COLS),
        ("twap_ticks", TWAP_COLS),
        ("candle_volume", CANDLE_COLS),
    ]
    ranged = [
        ("price_ticks", PRICE_COLS, 2_000_000),
        ("odds_ticks", ODDS_COLS, 1_000_000),
    ]

    for table, cols in intact:
        log(f"copying {table} in full...")
        try:
            copy_full(conn, table, cols)
        except sqlite3.DatabaseError as e:
            log(f"  {table} full copy failed ({e}); falling back to id ranges")
            conn.rollback()
            max_id = src_max_id(conn, table)
            copy_until_corrupt(conn, table, cols, max_id, start_step=50_000)

    for table, cols, step in ranged:
        log(f"copying {table} by id range...")
        max_id = src_max_id(conn, table)
        log(f"  {table}: probe max id {max_id:,}")
        last = copy_until_corrupt(conn, table, cols, max_id, start_step=step)
        log(f"  {table}: finished last_good_id={last:,}")

    for table in ("windows", "odds_ticks", "price_ticks", "twap_ticks", "candle_volume"):
        mx = conn.execute(f"SELECT COALESCE(MAX(id), 0) FROM main.{table}").fetchone()[0]
        n = conn.execute(f"SELECT COUNT(*) FROM main.{table}").fetchone()[0]
        conn.execute("DELETE FROM sqlite_sequence WHERE name=?", (table,))
        conn.execute("INSERT INTO sqlite_sequence(name, seq) VALUES(?, ?)", (table, mx))
        log(f"dest {table}: count={n:,} max_id={mx:,}")
    conn.commit()

    log("creating indexes...")
    t0 = time.time()
    conn.executescript(INDEXES_SQL)
    conn.commit()
    log(f"  indexes done in {time.time() - t0:.1f}s")

    log("switching dest to WAL...")
    conn.execute("DETACH DATABASE src")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.commit()

    log("PRAGMA quick_check on dest...")
    t0 = time.time()
    rows = conn.execute("PRAGMA quick_check").fetchall()
    log(f"quick_check ({time.time() - t0:.1f}s): {rows[:5]}")
    conn.close()
    if rows != [("ok",)]:
        log("DEST STILL NOT CLEAN")
        return 2
    log(f"salvage complete: {dest} ({dest.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
