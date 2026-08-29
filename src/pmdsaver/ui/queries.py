"""Read-only SQLite queries for the dashboard."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any


from pmdsaver.db import ensure_window_columns
from pmdsaver.runtime import data_dir


def db_path() -> Path:
    return data_dir() / "pmdsaver.db"


def connect() -> sqlite3.Connection:
    path = db_path()
    if not path.exists():
        raise FileNotFoundError(f"Database not found: {path}")
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    ensure_window_columns(conn)
    return conn


def current_window(conn: sqlite3.Connection) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT *
        FROM windows
        ORDER BY window_start DESC
        LIMIT 1
        """
    ).fetchone()
    if row is None:
        return None
    data = dict(row)
    data["seconds_remaining"] = max(0, int(data["window_end"]) - int(time.time()))
    data["price_to_beat"] = data.get("price_to_beat_gamma") or data.get("price_to_beat_rtds")
    return data


def list_windows(conn: sqlite3.Connection, limit: int = 20) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT id, slug, window_start, window_end,
               price_to_beat_rtds, price_to_beat_gamma,
               COALESCE(price_to_beat_gamma, price_to_beat_rtds) AS price_to_beat,
               final_price, outcome, outcome_source
        FROM windows
        ORDER BY window_start DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [dict(row) for row in rows]


def table_counts(conn: sqlite3.Connection, window_id: int | None = None) -> dict[str, int]:
    counts: dict[str, int] = {}
    for table in ("windows", "odds_ticks", "price_ticks", "candle_volume", "twap_ticks"):
        if window_id is None or table == "windows":
            counts[table] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        else:
            if table == "price_ticks":
                counts[table] = conn.execute(
                    "SELECT COUNT(*) FROM price_ticks WHERE window_id = ? OR window_id IS NULL",
                    (window_id,),
                ).fetchone()[0]
            else:
                counts[table] = conn.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE window_id = ?",
                    (window_id,),
                ).fetchone()[0]
    return counts


def latest_prices(conn: sqlite3.Connection, window_id: int | None = None) -> dict[str, Any]:
    sources = ("binance_spot", "binance_futures", "coinbase_spot", "bybit_spot")
    result: dict[str, Any] = {}
    for source in sources:
        if window_id is None:
            row = conn.execute(
                """
                SELECT price, size, recv_ts_ms, exchange_ts_ms
                FROM price_ticks
                WHERE source = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (source,),
            ).fetchone()
        else:
            row = conn.execute(
                """
                SELECT price, size, recv_ts_ms, exchange_ts_ms
                FROM price_ticks
                WHERE source = ? AND (window_id = ? OR window_id IS NULL)
                ORDER BY id DESC
                LIMIT 1
                """,
                (source, window_id),
            ).fetchone()
        result[source] = dict(row) if row else None
    return result


def latest_odds(conn: sqlite3.Connection, window_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT recv_ts_ms, up_bid, up_ask, up_mid,
               down_bid, down_ask, down_mid,
               last_trade_price, last_trade_side, event_type
        FROM odds_ticks
        WHERE window_id = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (window_id,),
    ).fetchone()
    return dict(row) if row else None


def latest_volume(conn: sqlite3.Connection, window_id: int, open_time_ms: int) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for source in ("binance_spot", "coinbase_spot"):
        row = conn.execute(
            """
            SELECT base_volume, quote_volume, is_closed, recv_ts_ms
            FROM candle_volume
            WHERE source = ? AND open_time_ms = ?
            ORDER BY recv_ts_ms DESC
            LIMIT 1
            """,
            (source, open_time_ms),
        ).fetchone()
        result[source] = dict(row) if row else None
    return result


def odds_series(conn: sqlite3.Connection, window_id: int, limit: int = 1000) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT recv_ts_ms, up_mid, down_mid, up_bid, up_ask, down_bid, down_ask
        FROM odds_ticks
        WHERE window_id = ? AND up_mid IS NOT NULL
          AND NOT (up_bid = '0.01' AND up_ask = '0.99')
          AND NOT (down_bid = '0.01' AND down_ask = '0.99')
        ORDER BY id DESC
        LIMIT ?
        """,
        (window_id, limit),
    ).fetchall()
    return [dict(row) for row in reversed(rows)]


def price_series(
    conn: sqlite3.Connection,
    window_id: int | None,
    limit: int = 1000,
) -> dict[str, list[dict[str, Any]]]:
    sources = ("binance_spot", "binance_futures", "coinbase_spot", "bybit_spot")
    series: dict[str, list[dict[str, Any]]] = {}
    for source in sources:
        if window_id is None:
            rows = conn.execute(
                """
                SELECT recv_ts_ms, price
                FROM price_ticks
                WHERE source = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (source, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT recv_ts_ms, price
                FROM price_ticks
                WHERE source = ? AND (window_id = ? OR window_id IS NULL)
                ORDER BY id DESC
                LIMIT ?
                """,
                (source, window_id, limit),
            ).fetchall()
        series[source] = [dict(row) for row in reversed(rows)]
    return series


def twap_series(conn: sqlite3.Connection, window_id: int, limit: int = 500) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT recv_ts_ms, value
        FROM twap_ticks
        WHERE window_id = ?
        ORDER BY id DESC
        LIMIT ?
        """,
        (window_id, limit),
    ).fetchall()
    return [dict(row) for row in reversed(rows)]


def volume_series(conn: sqlite3.Connection, window_id: int, limit: int = 500) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT recv_ts_ms, base_volume AS value
        FROM candle_volume
        WHERE window_id = ? AND source = 'binance_spot' AND base_volume IS NOT NULL
        ORDER BY recv_ts_ms DESC
        LIMIT ?
        """,
        (window_id, limit),
    ).fetchall()
    return [dict(row) for row in reversed(rows)]


def recent_odds(conn: sqlite3.Connection, window_id: int, limit: int = 50) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT recv_ts_ms, event_type, up_mid, down_mid,
               last_trade_price, last_trade_side
        FROM odds_ticks
        WHERE window_id = ?
        ORDER BY id DESC
        LIMIT ?
        """,
        (window_id, limit),
    ).fetchall()
    return [dict(row) for row in rows]


def recent_prices(conn: sqlite3.Connection, window_id: int | None, limit: int = 50) -> list[dict[str, Any]]:
    if window_id is None:
        rows = conn.execute(
            """
            SELECT recv_ts_ms, source, price, size
            FROM price_ticks
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT recv_ts_ms, source, price, size
            FROM price_ticks
            WHERE window_id = ? OR window_id IS NULL
            ORDER BY id DESC
            LIMIT ?
            """,
            (window_id, limit),
        ).fetchall()
    return [dict(row) for row in rows]


def live_feed(
    conn: sqlite3.Connection,
    window_id: int,
    *,
    since_odds_id: int = 0,
    since_price_id: int = 0,
    since_twap_id: int = 0,
    tail_limit: int = 150,
) -> dict[str, Any]:
    window_row = conn.execute("SELECT * FROM windows WHERE id = ?", (window_id,)).fetchone()
    if window_row is None:
        return {"window": None}

    window = dict(window_row)
    window["seconds_remaining"] = max(0, int(window["window_end"]) - int(time.time()))
    window["price_to_beat"] = window.get("price_to_beat_gamma") or window.get("price_to_beat_rtds")
    open_time_ms = int(window["window_start"]) * 1000
    now_ms = int(time.time() * 1000)
    one_sec_ago = now_ms - 1000

    cursors = {
        "odds_id": conn.execute(
            "SELECT COALESCE(MAX(id), 0) FROM odds_ticks WHERE window_id = ?",
            (window_id,),
        ).fetchone()[0],
        "price_id": conn.execute(
            "SELECT COALESCE(MAX(id), 0) FROM price_ticks WHERE window_id = ? OR window_id IS NULL",
            (window_id,),
        ).fetchone()[0],
        "twap_id": conn.execute(
            "SELECT COALESCE(MAX(id), 0) FROM twap_ticks WHERE window_id = ?",
            (window_id,),
        ).fetchone()[0],
    }

    ingest = {
        "odds_per_sec": conn.execute(
            "SELECT COUNT(*) FROM odds_ticks WHERE window_id = ? AND recv_ts_ms >= ?",
            (window_id, one_sec_ago),
        ).fetchone()[0],
        "prices_per_sec": conn.execute(
            """
            SELECT COUNT(*) FROM price_ticks
            WHERE (window_id = ? OR window_id IS NULL) AND recv_ts_ms >= ?
            """,
            (window_id, one_sec_ago),
        ).fetchone()[0],
        "twap_per_sec": conn.execute(
            "SELECT COUNT(*) FROM twap_ticks WHERE window_id = ? AND recv_ts_ms >= ?",
            (window_id, one_sec_ago),
        ).fetchone()[0],
    }

    price_rates: dict[str, int] = {}
    for source in ("binance_spot", "binance_futures", "coinbase_spot", "bybit_spot"):
        price_rates[source] = conn.execute(
            """
            SELECT COUNT(*) FROM price_ticks
            WHERE source = ? AND (window_id = ? OR window_id IS NULL) AND recv_ts_ms >= ?
            """,
            (source, window_id, one_sec_ago),
        ).fetchone()[0]
    ingest["prices_by_source"] = price_rates

    if since_odds_id == 0:
        new_odds = conn.execute(
            """
            SELECT id, recv_ts_ms, up_mid, down_mid, up_bid, up_ask, down_bid, down_ask,
                   event_type, last_trade_price, last_trade_side
            FROM odds_ticks
            WHERE window_id = ?
              AND up_mid IS NOT NULL
              AND NOT (up_bid = '0.01' AND up_ask = '0.99')
              AND NOT (down_bid = '0.01' AND down_ask = '0.99')
            ORDER BY id DESC
            LIMIT ?
            """,
            (window_id, tail_limit),
        ).fetchall()
        new_odds = list(reversed(new_odds))
    else:
        new_odds = conn.execute(
            """
            SELECT id, recv_ts_ms, up_mid, down_mid, up_bid, up_ask, down_bid, down_ask,
                   event_type, last_trade_price, last_trade_side
            FROM odds_ticks
            WHERE window_id = ? AND id > ?
              AND up_mid IS NOT NULL
              AND NOT (up_bid = '0.01' AND up_ask = '0.99')
              AND NOT (down_bid = '0.01' AND down_ask = '0.99')
            ORDER BY id ASC
            LIMIT ?
            """,
            (window_id, since_odds_id, tail_limit),
        ).fetchall()

    if since_price_id == 0:
        new_prices = conn.execute(
            """
            SELECT id, recv_ts_ms, source, price, size
            FROM price_ticks
            WHERE window_id = ? OR window_id IS NULL
            ORDER BY id DESC
            LIMIT ?
            """,
            (window_id, tail_limit),
        ).fetchall()
        new_prices = list(reversed(new_prices))
    else:
        new_prices = conn.execute(
            """
            SELECT id, recv_ts_ms, source, price, size
            FROM price_ticks
            WHERE (window_id = ? OR window_id IS NULL) AND id > ?
            ORDER BY id ASC
            LIMIT ?
            """,
            (window_id, since_price_id, tail_limit),
        ).fetchall()

    if since_twap_id == 0:
        new_twap = conn.execute(
            """
            SELECT id, recv_ts_ms, value
            FROM twap_ticks
            WHERE window_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (window_id, tail_limit),
        ).fetchall()
        new_twap = list(reversed(new_twap))
    else:
        new_twap = conn.execute(
            """
            SELECT id, recv_ts_ms, value
            FROM twap_ticks
            WHERE window_id = ? AND id > ?
            ORDER BY id ASC
            LIMIT ?
            """,
            (window_id, since_twap_id, tail_limit),
        ).fetchall()

    return {
        "server_ts_ms": now_ms,
        "window": window,
        "counts": table_counts(conn, window_id),
        "cursors": cursors,
        "ingest": ingest,
        "latest_odds": latest_odds(conn, window_id),
        "latest_prices": latest_prices(conn, window_id),
        "latest_volume": latest_volume(conn, window_id, open_time_ms),
        "new_odds": [dict(row) for row in new_odds],
        "new_prices": [dict(row) for row in new_prices],
        "new_twap": [dict(row) for row in new_twap],
        "db_path": str(db_path()),
    }


PRICE_SOURCES = ("binance_spot", "binance_futures", "coinbase_spot", "bybit_spot")


def _rows(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute(sql, params).fetchall()]


def all_odds_ticks(conn: sqlite3.Connection, window_id: int) -> list[dict[str, Any]]:
    return _rows(
        conn,
        """
        SELECT id, recv_ts_ms, exchange_ts_ms, event_type,
               up_bid, up_ask, up_mid, down_bid, down_ask, down_mid,
               last_trade_token, last_trade_price, last_trade_size, last_trade_side
        FROM odds_ticks
        WHERE window_id = ?
          AND NOT (up_bid = '0.01' AND up_ask = '0.99')
          AND NOT (down_bid = '0.01' AND down_ask = '0.99')
        ORDER BY recv_ts_ms ASC, id ASC
        """,
        (window_id,),
    )


def all_price_ticks(
    conn: sqlite3.Connection,
    window_id: int,
    start_ms: int,
    end_ms: int,
) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for source in PRICE_SOURCES:
        out[source] = _rows(
            conn,
            """
            SELECT id, recv_ts_ms, exchange_ts_ms, source, price, size
            FROM price_ticks
            WHERE source = ?
              AND recv_ts_ms BETWEEN ? AND ?
              AND (window_id = ? OR window_id IS NULL)
            ORDER BY recv_ts_ms ASC, id ASC
            """,
            (source, start_ms, end_ms, window_id),
        )
    return out


def all_twap_ticks(conn: sqlite3.Connection, window_id: int) -> list[dict[str, Any]]:
    return _rows(
        conn,
        """
        SELECT id, recv_ts_ms, exchange_ts_ms, symbol, value
        FROM twap_ticks
        WHERE window_id = ?
        ORDER BY recv_ts_ms ASC, id ASC
        """,
        (window_id,),
    )


def all_candle_volume(conn: sqlite3.Connection, window_id: int) -> list[dict[str, Any]]:
    return _rows(
        conn,
        """
        SELECT id, recv_ts_ms, source, open_time_ms, base_volume, quote_volume, is_closed
        FROM candle_volume
        WHERE window_id = ?
        ORDER BY recv_ts_ms ASC, id ASC
        """,
        (window_id,),
    )


def window_replay(conn: sqlite3.Connection, window_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM windows WHERE id = ?", (window_id,)).fetchone()
    if row is None:
        return None
    window = dict(row)
    start_ms = int(window["window_start"]) * 1000
    end_ms = int(window["window_end"]) * 1000 + 2000
    window["price_to_beat"] = window.get("price_to_beat_gamma") or window.get("price_to_beat_rtds")
    prices = all_price_ticks(conn, window_id, start_ms, end_ms)
    odds = all_odds_ticks(conn, window_id)
    twap = all_twap_ticks(conn, window_id)
    volume = all_candle_volume(conn, window_id)
    return {
        "window": window,
        "counts": {
            "odds": len(odds),
            "twap": len(twap),
            "volume": len(volume),
            **{source: len(rows) for source, rows in prices.items()},
        },
        "odds": odds,
        "prices": prices,
        "twap": twap,
        "volume": volume,
    }
