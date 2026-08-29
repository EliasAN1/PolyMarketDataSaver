"""Cache a compact per-second tape for each closed window.

Tick replay (iter_snapshots) never changes for a closed window, only the
strategy variables do. So we sample every window once into a dense
1-second-resolution tape and cache it in a separate SQLite file
(data/lab_cache.db). The Strategy Lab frontend then re-evaluates the combo
strategy against that tape entirely in the browser, so changing a variable
never touches SQLite again.

Row layout (index = elapsed seconds into the window, 0..duration):
    [up_ask, down_ask, up_mid, down_mid, btc_minus_ptb, twap_minus_ptb,
     volume, venues_up, venues_down]
Missing ticks are forward-filled from the last known state; seconds before
the first book snapshot stay null (no possible entry there anyway).
"""

from __future__ import annotations

import json
import sqlite3
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Iterator

from pmdsaver.backtest import engine as bt_engine
from pmdsaver.backtest.replay import (
    Snapshot,
    WindowTape,
    iter_snapshots,
    load_closed_windows,
    resolve_window,
)
from pmdsaver.runtime import data_dir

CACHE_FILE_NAME = "lab_cache.db"
ROUND_PRICE = 4
ROUND_DIST = 3
ROUND_VOLUME = 3

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tapes (
    window_id INTEGER PRIMARY KEY,
    slug TEXT NOT NULL,
    window_start INTEGER NOT NULL,
    window_end INTEGER NOT NULL,
    ptb REAL,
    outcome TEXT,
    duration INTEGER,
    skip_reason TEXT,
    payload TEXT,
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tapes_window_start ON tapes(window_start);
CREATE INDEX IF NOT EXISTS idx_tapes_slug ON tapes(slug);
"""


def cache_path() -> Path:
    return data_dir() / CACHE_FILE_NAME


def connect_cache() -> sqlite3.Connection:
    path = cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.executescript(_SCHEMA)
    conn.commit()
    return conn


def _r(value: float | None, digits: int) -> float | None:
    return None if value is None else round(value, digits)


def sample_window_rows(conn: sqlite3.Connection, tape: WindowTape) -> list[list[Any]] | None:
    """Dense 1-second tape for one window, or None if it has no usable book."""
    if tape.skip_reason:
        return None
    duration = int(round(tape.window_end - tape.window_start))
    if duration <= 0:
        return None
    ptb = float(tape.ptb)
    n = duration + 1
    rows: list[list[Any] | None] = [None] * n
    current: list[Any] = [None] * 9
    last_idx = -1

    def row_from(snap: Snapshot) -> list[Any]:
        btc_minus = None if snap.btc is None else snap.btc - ptb
        twap_minus = None if snap.twap is None else snap.twap - ptb
        return [
            _r(snap.up_ask, ROUND_PRICE),
            _r(snap.down_ask, ROUND_PRICE),
            _r(snap.up_mid, ROUND_PRICE),
            _r(snap.down_mid, ROUND_PRICE),
            _r(btc_minus, ROUND_DIST),
            _r(twap_minus, ROUND_DIST),
            _r(snap.volume_base, ROUND_VOLUME),
            snap.venues_on_side("up"),
            snap.venues_on_side("down"),
        ]

    for snap in iter_snapshots(conn, tape):
        idx = min(duration, max(0, round(snap.elapsed_s)))
        if idx > last_idx:
            for j in range(last_idx + 1, idx):
                rows[j] = current
            last_idx = idx
        current = row_from(snap)
        rows[idx] = current

    if last_idx < 0:
        return None
    for j in range(last_idx + 1, n):
        rows[j] = current
    return rows  # type: ignore[return-value]


def _meta_for(tape: WindowTape) -> dict[str, Any]:
    return {
        "slug": tape.slug,
        "window_start": tape.window_start,
        "window_end": tape.window_end,
        "ptb": float(tape.ptb) if tape.ptb else None,
        "outcome": tape.outcome or None,
    }


def _sample_row(conn: sqlite3.Connection, row: sqlite3.Row) -> tuple[int, dict[str, Any], list[list[Any]] | None, str | None]:
    window_id = int(row["id"])
    tape = resolve_window(conn, row)
    rows = sample_window_rows(conn, tape)
    reason = tape.skip_reason or (None if rows is not None else "no_snapshot")
    return window_id, _meta_for(tape), rows, reason


def _sample_job(window_id: int) -> tuple[int, dict[str, Any], list[list[Any]] | None, str | None]:
    conn = bt_engine._WORKER_CONN
    if conn is None:
        raise RuntimeError("Worker SQLite connection was not initialized")
    row = conn.execute("SELECT * FROM windows WHERE id = ?", (window_id,)).fetchone()
    if row is None:
        raise RuntimeError(f"Window {window_id} not found")
    return _sample_row(conn, row)


def _store(
    cache_conn: sqlite3.Connection,
    window_id: int,
    meta: dict[str, Any],
    rows: list[list[Any]] | None,
    reason: str | None,
) -> None:
    payload = json.dumps(rows, separators=(",", ":")) if rows is not None else None
    start = meta.get("window_start")
    end = meta.get("window_end")
    duration = int(round(end - start)) if start is not None and end is not None else None
    cache_conn.execute(
        """
        INSERT INTO tapes
            (window_id, slug, window_start, window_end, ptb, outcome, duration, skip_reason, payload, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(window_id) DO UPDATE SET
            slug=excluded.slug, window_start=excluded.window_start, window_end=excluded.window_end,
            ptb=excluded.ptb, outcome=excluded.outcome, duration=excluded.duration,
            skip_reason=excluded.skip_reason, payload=excluded.payload, created_at=excluded.created_at
        """,
        (
            window_id,
            meta.get("slug"),
            start,
            end,
            meta.get("ptb"),
            meta.get("outcome"),
            duration,
            reason,
            payload,
            int(time.time()),
        ),
    )


def scan_new_windows(
    conn: sqlite3.Connection,
    cache_conn: sqlite3.Connection,
    *,
    slug: str | None = None,
    start_ts: int | None = None,
    end_ts: int | None = None,
    workers: int = 0,
) -> Iterator[dict[str, Any]]:
    """Sample any window in range that isn't cached yet. Yields progress events."""
    windows = load_closed_windows(conn, slug=slug, start_ts=start_ts, end_ts=end_ts)
    cached_ids = {
        int(r["window_id"]) for r in cache_conn.execute("SELECT window_id FROM tapes").fetchall()
    }
    missing = [row for row in windows if int(row["id"]) not in cached_ids]
    total = len(windows)
    total_missing = len(missing)
    yield {
        "type": "scan_start",
        "total": total,
        "cached": total - total_missing,
        "missing": total_missing,
    }
    if not missing:
        yield {"type": "scan_done", "scanned": 0}
        return

    n_workers = bt_engine._worker_count(workers, total_missing)
    done = 0
    if n_workers == 1:
        for row in missing:
            window_id, meta, rows, reason = _sample_row(conn, row)
            _store(cache_conn, window_id, meta, rows, reason)
            done += 1
            if done % 10 == 0 or done == total_missing:
                cache_conn.commit()
            yield {"type": "scan_progress", "done": done, "left": total_missing - done, "total": total_missing}
    else:
        db_path = bt_engine._db_file(conn)
        with ProcessPoolExecutor(
            max_workers=n_workers,
            initializer=bt_engine._init_worker,
            initargs=(db_path,),
        ) as pool:
            futures = [pool.submit(_sample_job, int(row["id"])) for row in missing]
            for fut in as_completed(futures):
                window_id, meta, rows, reason = fut.result()
                _store(cache_conn, window_id, meta, rows, reason)
                done += 1
                if done % 10 == 0 or done == total_missing:
                    cache_conn.commit()
                yield {"type": "scan_progress", "done": done, "left": total_missing - done, "total": total_missing}
    cache_conn.commit()
    yield {"type": "scan_done", "scanned": done}


def _range_clauses(slug: str | None, start_ts: int | None, end_ts: int | None) -> tuple[list[str], list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if slug:
        clauses.append("slug = ?")
        params.append(slug)
    if start_ts is not None:
        clauses.append("window_start >= ?")
        params.append(start_ts)
    if end_ts is not None:
        clauses.append("window_end <= ?")
        params.append(end_ts)
    return clauses, params


def load_tape(
    cache_conn: sqlite3.Connection,
    *,
    slug: str | None = None,
    start_ts: int | None = None,
    end_ts: int | None = None,
) -> dict[str, Any]:
    clauses, params = _range_clauses(slug, start_ts, end_ts)
    where = " AND ".join(["payload IS NOT NULL", *clauses]) if clauses else "payload IS NOT NULL"
    rows = cache_conn.execute(
        f"""
        SELECT window_id, slug, window_start, window_end, ptb, outcome, payload
        FROM tapes
        WHERE {where}
        ORDER BY window_start ASC
        """,
        params,
    ).fetchall()
    windows = [
        {
            "id": int(row["window_id"]),
            "slug": row["slug"],
            "start": int(row["window_start"]),
            "end": int(row["window_end"]),
            "ptb": row["ptb"],
            "outcome": row["outcome"],
            "rows": json.loads(row["payload"]),
        }
        for row in rows
    ]

    skip_where = " AND ".join(["payload IS NULL", *clauses]) if clauses else "payload IS NULL"
    skip_rows = cache_conn.execute(
        f"SELECT skip_reason, COUNT(*) AS n FROM tapes WHERE {skip_where} GROUP BY skip_reason",
        params,
    ).fetchall()
    skip_counts = {(row["skip_reason"] or "skipped"): int(row["n"]) for row in skip_rows}

    return {"windows": windows, "skip_counts": skip_counts, "count": len(windows)}
