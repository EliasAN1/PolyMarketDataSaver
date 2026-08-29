"""SQLite persistence with WAL and batched async writes."""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import aiosqlite

logger = logging.getLogger(__name__)

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;

CREATE TABLE IF NOT EXISTS windows (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT NOT NULL UNIQUE,
    window_start INTEGER NOT NULL,
    window_end INTEGER NOT NULL,
    condition_id TEXT,
    up_token_id TEXT NOT NULL,
    down_token_id TEXT NOT NULL,
    price_to_beat_rtds TEXT,
    price_to_beat_gamma TEXT,
    price_to_beat_source TEXT,
    final_price TEXT,
    outcome TEXT,
    outcome_source TEXT,
    created_at_ms INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS odds_ticks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    window_id INTEGER NOT NULL,
    recv_ts_ms INTEGER NOT NULL,
    exchange_ts_ms INTEGER,
    event_type TEXT NOT NULL,
    up_bid TEXT,
    up_ask TEXT,
    up_mid TEXT,
    down_bid TEXT,
    down_ask TEXT,
    down_mid TEXT,
    last_trade_token TEXT,
    last_trade_price TEXT,
    last_trade_size TEXT,
    last_trade_side TEXT,
    FOREIGN KEY (window_id) REFERENCES windows(id)
);

CREATE INDEX IF NOT EXISTS idx_odds_ticks_window_ts
    ON odds_ticks(window_id, recv_ts_ms);

CREATE TABLE IF NOT EXISTS price_ticks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recv_ts_ms INTEGER NOT NULL,
    exchange_ts_ms INTEGER,
    source TEXT NOT NULL,
    price TEXT NOT NULL,
    size TEXT,
    window_id INTEGER,
    FOREIGN KEY (window_id) REFERENCES windows(id)
);

CREATE INDEX IF NOT EXISTS idx_price_ticks_source_ts
    ON price_ticks(source, recv_ts_ms);

CREATE TABLE IF NOT EXISTS candle_volume (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recv_ts_ms INTEGER NOT NULL,
    source TEXT NOT NULL,
    open_time_ms INTEGER NOT NULL,
    base_volume TEXT,
    quote_volume TEXT,
    is_closed INTEGER NOT NULL DEFAULT 0,
    window_id INTEGER,
    FOREIGN KEY (window_id) REFERENCES windows(id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_candle_volume_source_open
    ON candle_volume(source, open_time_ms);

CREATE TABLE IF NOT EXISTS twap_ticks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recv_ts_ms INTEGER NOT NULL,
    exchange_ts_ms INTEGER,
    symbol TEXT NOT NULL,
    value TEXT NOT NULL,
    window_id INTEGER,
    FOREIGN KEY (window_id) REFERENCES windows(id)
);

CREATE INDEX IF NOT EXISTS idx_twap_ticks_window_ts
    ON twap_ticks(window_id, recv_ts_ms);
"""

WINDOW_EXTRA_COLUMNS = ("price_to_beat_source", "final_price", "outcome", "outcome_source")


def ensure_window_columns(conn: sqlite3.Connection) -> None:
    names = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='windows'"
        )
    }
    if "windows" not in names:
        return
    columns = {row[1] for row in conn.execute("PRAGMA table_info(windows)")}
    changed = False
    for name in WINDOW_EXTRA_COLUMNS:
        if name not in columns:
            conn.execute(f"ALTER TABLE windows ADD COLUMN {name} TEXT")
            changed = True
    if changed:
        conn.commit()


@dataclass(slots=True)
class WindowRow:
    slug: str
    window_start: int
    window_end: int
    condition_id: str | None
    up_token_id: str
    down_token_id: str
    price_to_beat_rtds: str | None = None
    price_to_beat_gamma: str | None = None
    price_to_beat_source: str | None = None


class Database:
    def __init__(
        self,
        path: Path,
        flush_interval_s: float = 0.2,
        flush_batch_size: int = 1000,
    ) -> None:
        self.path = path
        self.flush_interval_s = flush_interval_s
        self.flush_batch_size = flush_batch_size
        self._conn: aiosqlite.Connection | None = None
        self._queue: asyncio.Queue[tuple[str, tuple[Any, ...]]] = asyncio.Queue()
        self._writer_task: asyncio.Task[None] | None = None
        self._window_ids: dict[str, int] = {}

    async def open(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self.path)
        await self._conn.executescript(SCHEMA)
        await self._migrate_windows()
        await self._conn.commit()
        self._writer_task = asyncio.create_task(self._writer_loop(), name="db-writer")

    async def _migrate_windows(self) -> None:
        assert self._conn is not None
        cursor = await self._conn.execute("PRAGMA table_info(windows)")
        columns = {row[1] for row in await cursor.fetchall()}
        for name in WINDOW_EXTRA_COLUMNS:
            if name not in columns:
                await self._conn.execute(f"ALTER TABLE windows ADD COLUMN {name} TEXT")

    async def close(self) -> None:
        if self._writer_task is not None:
            await self._queue.put(("__flush__", ()))
            await self._queue.put(("__stop__", ()))
            await self._writer_task
            self._writer_task = None
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def upsert_window(self, row: WindowRow) -> int:
        assert self._conn is not None
        now_ms = int(time.time() * 1000)
        await self._conn.execute(
            """
            INSERT INTO windows (
                slug, window_start, window_end, condition_id,
                up_token_id, down_token_id, price_to_beat_rtds,
                price_to_beat_gamma, price_to_beat_source, created_at_ms
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(slug) DO UPDATE SET
                condition_id = COALESCE(excluded.condition_id, windows.condition_id),
                up_token_id = excluded.up_token_id,
                down_token_id = excluded.down_token_id,
                price_to_beat_rtds = COALESCE(excluded.price_to_beat_rtds, windows.price_to_beat_rtds),
                price_to_beat_gamma = COALESCE(excluded.price_to_beat_gamma, windows.price_to_beat_gamma),
                price_to_beat_source = COALESCE(excluded.price_to_beat_source, windows.price_to_beat_source)
            """,
            (
                row.slug,
                row.window_start,
                row.window_end,
                row.condition_id,
                row.up_token_id,
                row.down_token_id,
                row.price_to_beat_rtds,
                row.price_to_beat_gamma,
                row.price_to_beat_source,
                now_ms,
            ),
        )
        await self._conn.commit()
        cursor = await self._conn.execute(
            "SELECT id FROM windows WHERE slug = ?",
            (row.slug,),
        )
        result = await cursor.fetchone()
        assert result is not None
        window_id = int(result[0])
        self._window_ids[row.slug] = window_id
        return window_id

    async def update_window_price_to_beat(
        self,
        slug: str,
        *,
        price_to_beat_rtds: str | None = None,
        price_to_beat_gamma: str | None = None,
    ) -> None:
        assert self._conn is not None
        await self._conn.execute(
            """
            UPDATE windows SET
                price_to_beat_rtds = COALESCE(?, price_to_beat_rtds),
                price_to_beat_gamma = COALESCE(?, price_to_beat_gamma)
            WHERE slug = ?
            """,
            (price_to_beat_rtds, price_to_beat_gamma, slug),
        )
        await self._conn.commit()

    async def set_window_price_to_beat_rtds(
        self,
        slug: str,
        value: str,
        source: str | None = None,
    ) -> None:
        assert self._conn is not None
        await self._conn.execute(
            """
            UPDATE windows SET
                price_to_beat_rtds = ?,
                price_to_beat_source = COALESCE(?, price_to_beat_source)
            WHERE slug = ?
            """,
            (value, source, slug),
        )
        await self._conn.commit()

    async def set_window_settlement(
        self,
        slug: str,
        *,
        final_price: str | None,
        outcome: str | None,
        price_to_beat_gamma: str | None = None,
        outcome_source: str | None = None,
    ) -> None:
        assert self._conn is not None
        await self._conn.execute(
            """
            UPDATE windows SET
                final_price = COALESCE(?, final_price),
                outcome = COALESCE(?, outcome),
                price_to_beat_gamma = COALESCE(?, price_to_beat_gamma),
                outcome_source = COALESCE(?, outcome_source)
            WHERE slug = ?
            """,
            (final_price, outcome, price_to_beat_gamma, outcome_source, slug),
        )
        await self._conn.commit()

    async def list_unsettled_slugs(self, *, before_ts: int, limit: int = 8) -> list[str]:
        """Past windows that still have no Polymarket-verified outcome."""
        assert self._conn is not None
        cursor = await self._conn.execute(
            """
            SELECT slug FROM windows
            WHERE window_end <= ?
              AND (outcome IS NULL OR outcome = '')
            ORDER BY window_end ASC
            LIMIT ?
            """,
            (before_ts, limit),
        )
        rows = await cursor.fetchall()
        return [str(row[0]) for row in rows]

    async def load_window_ptb(self, slug: str) -> tuple[str | None, str | None]:
        assert self._conn is not None
        cursor = await self._conn.execute(
            """
            SELECT price_to_beat_rtds, price_to_beat_gamma
            FROM windows WHERE slug = ?
            """,
            (slug,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None, None
        return row[0], row[1]

    def window_id(self, slug: str) -> int | None:
        return self._window_ids.get(slug)

    async def enqueue_odds_tick(self, row: tuple[Any, ...]) -> None:
        await self._queue.put(("odds_ticks", row))

    async def enqueue_price_tick(self, row: tuple[Any, ...]) -> None:
        await self._queue.put(("price_ticks", row))

    async def enqueue_candle_volume(self, row: tuple[Any, ...]) -> None:
        await self._queue.put(("candle_volume", row))

    async def enqueue_twap_tick(self, row: tuple[Any, ...]) -> None:
        await self._queue.put(("twap_ticks", row))

    async def _writer_loop(self) -> None:
        assert self._conn is not None
        pending: dict[str, list[tuple[Any, ...]]] = {
            "odds_ticks": [],
            "price_ticks": [],
            "candle_volume": [],
            "twap_ticks": [],
        }
        insert_sql = {
            "odds_ticks": """
                INSERT INTO odds_ticks (
                    window_id, recv_ts_ms, exchange_ts_ms, event_type,
                    up_bid, up_ask, up_mid, down_bid, down_ask, down_mid,
                    last_trade_token, last_trade_price, last_trade_size, last_trade_side
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            "price_ticks": """
                INSERT INTO price_ticks (
                    recv_ts_ms, exchange_ts_ms, source, price, size, window_id
                ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            "candle_volume": """
                INSERT INTO candle_volume (
                    recv_ts_ms, source, open_time_ms, base_volume, quote_volume,
                    is_closed, window_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source, open_time_ms) DO UPDATE SET
                    recv_ts_ms = excluded.recv_ts_ms,
                    base_volume = excluded.base_volume,
                    quote_volume = excluded.quote_volume,
                    is_closed = excluded.is_closed,
                    window_id = COALESCE(excluded.window_id, candle_volume.window_id)
            """,
            "twap_ticks": """
                INSERT INTO twap_ticks (
                    recv_ts_ms, exchange_ts_ms, symbol, value, window_id
                ) VALUES (?, ?, ?, ?, ?)
            """,
        }

        while True:
            try:
                item = await asyncio.wait_for(
                    self._queue.get(),
                    timeout=self.flush_interval_s,
                )
            except asyncio.TimeoutError:
                item = ("__flush__", ())

            table, row = item
            if table == "__stop__":
                await self._flush(pending, insert_sql)
                return
            if table == "__flush__":
                await self._flush(pending, insert_sql)
                continue

            pending[table].append(row)
            total = sum(len(rows) for rows in pending.values())
            if total >= self.flush_batch_size:
                await self._flush(pending, insert_sql)

    async def _flush(
        self,
        pending: dict[str, list[tuple[Any, ...]]],
        insert_sql: dict[str, str],
    ) -> None:
        assert self._conn is not None
        total = sum(len(rows) for rows in pending.values())
        if total == 0:
            return

        try:
            for table, rows in pending.items():
                if not rows:
                    continue
                await self._conn.executemany(insert_sql[table], rows)
                rows.clear()
            await self._conn.commit()
        except Exception:
            logger.exception("Failed flushing %s rows to SQLite", total)
            raise
