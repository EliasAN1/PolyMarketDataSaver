"""Tick replay for one 5m window."""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from typing import Any, Iterator

from pmdsaver.outcome import binary_outcome

LATE_JOIN_SECONDS = 45
PRICE_BUCKET_MS = 100
TWAP_NEAR_END_BEFORE_MS = 5_000
TWAP_NEAR_END_AFTER_MS = 30_000
END_GRACE_MS = 2_000


@dataclass(slots=True)
class Snapshot:
    ts_ms: int
    elapsed_s: float
    seconds_left: float
    slug: str
    window_id: int
    ptb: float
    btc: float | None = None
    twap: float | None = None
    volume_base: float | None = None
    volume_coinbase: float | None = None
    up_bid: float | None = None
    up_ask: float | None = None
    up_mid: float | None = None
    down_bid: float | None = None
    down_ask: float | None = None
    down_mid: float | None = None
    binance_spot: float | None = None
    coinbase_spot: float | None = None
    bybit_spot: float | None = None
    binance_futures: float | None = None

    def fill_price(self, side: str, mode: str) -> float | None:
        if side == "up":
            return self.up_mid if mode == "mid" else self.up_ask
        return self.down_mid if mode == "mid" else self.down_ask

    def has_book(self) -> bool:
        return self.up_ask is not None or self.down_ask is not None

    def venue_prices(self) -> dict[str, float]:
        out: dict[str, float] = {}
        for name, value in (
            ("binance_spot", self.binance_spot),
            ("coinbase_spot", self.coinbase_spot),
            ("bybit_spot", self.bybit_spot),
            ("binance_futures", self.binance_futures),
        ):
            if value is not None:
                out[name] = value
        return out

    def venues_on_side(self, side: str) -> int:
        count = 0
        for price in self.venue_prices().values():
            if side == "up" and price > self.ptb:
                count += 1
            elif side == "down" and price < self.ptb:
                count += 1
        return count


@dataclass(slots=True)
class WindowTape:
    window_id: int
    slug: str
    window_start: int
    window_end: int
    ptb: str
    ptb_source: str | None
    final_price: str
    outcome: str
    resolution_source: str
    skip_reason: str | None = None


def _f(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _is_placeholder_book(row: sqlite3.Row) -> bool:
    return (row["up_bid"] == "0.01" and row["up_ask"] == "0.99") or (
        row["down_bid"] == "0.01" and row["down_ask"] == "0.99"
    )


def window_ptb(row: sqlite3.Row) -> tuple[str | None, str | None]:
    gamma = row["price_to_beat_gamma"] if "price_to_beat_gamma" in row.keys() else None
    rtds = row["price_to_beat_rtds"] if "price_to_beat_rtds" in row.keys() else None
    source = None
    if "price_to_beat_source" in row.keys():
        source = row["price_to_beat_source"]
    if gamma:
        return str(gamma), source or "gamma"
    if rtds:
        return str(rtds), source or "rtds"
    return None, source


def load_closed_windows(
    conn: sqlite3.Connection,
    *,
    slug: str | None = None,
    start_ts: int | None = None,
    end_ts: int | None = None,
    now_ts: int | None = None,
) -> list[sqlite3.Row]:
    now = int(now_ts if now_ts is not None else time.time())
    clauses = ["window_end <= ?"]
    params: list[Any] = [now]
    if slug:
        clauses.append("slug = ?")
        params.append(slug)
    if start_ts is not None:
        clauses.append("window_start >= ?")
        params.append(start_ts)
    if end_ts is not None:
        clauses.append("window_end <= ?")
        params.append(end_ts)
    sql = f"""
        SELECT *
        FROM windows
        WHERE {' AND '.join(clauses)}
        ORDER BY window_start ASC
    """
    return conn.execute(sql, params).fetchall()


def resolve_window(conn: sqlite3.Connection, row: sqlite3.Row) -> WindowTape:
    window_id = int(row["id"])
    slug = str(row["slug"])
    start = int(row["window_start"])
    end = int(row["window_end"])
    ptb, ptb_source = window_ptb(row)
    stored_final = row["final_price"] if "final_price" in row.keys() else None
    stored_outcome = row["outcome"] if "outcome" in row.keys() else None
    stored_source = None
    if "outcome_source" in row.keys() and row["outcome_source"]:
        stored_source = str(row["outcome_source"])

    if ptb is None:
        return WindowTape(
            window_id, slug, start, end, "", ptb_source, "", "", "", "no_ptb"
        )

    final = str(stored_final) if stored_final else None
    verified = stored_outcome in ("up", "down")
    source = stored_source if verified else ("gamma" if final else None)
    if final is None:
        final, guessed = _twap_near_end(conn, window_id, end)
        if not verified:
            source = guessed
    if final is None:
        final, guessed = _spot_at_end(conn, window_id, start, end, "binance_spot")
        if not verified:
            source = guessed
    if final is None:
        final, guessed = _spot_at_end(conn, window_id, start, end, "coinbase_spot")
        if not verified:
            source = guessed

    outcome = stored_outcome if verified else binary_outcome(final, ptb)
    if not verified and final is None:
        return WindowTape(
            window_id, slug, start, end, ptb, ptb_source, "", "", "", "no_resolution"
        )
    if outcome is None:
        return WindowTape(
            window_id,
            slug,
            start,
            end,
            ptb,
            ptb_source,
            final or "",
            "",
            source or "unknown",
            "tie",
        )

    first_odds_ms = _first_odds_ms(conn, window_id)
    close = final or ""
    if first_odds_ms is None:
        return WindowTape(
            window_id, slug, start, end, ptb, ptb_source, close, outcome, source or "unknown", "no_odds"
        )
    if first_odds_ms > (start + LATE_JOIN_SECONDS) * 1000:
        return WindowTape(
            window_id,
            slug,
            start,
            end,
            ptb,
            ptb_source,
            close,
            outcome,
            source or "unknown",
            "late_join",
        )

    return WindowTape(
        window_id, slug, start, end, ptb, ptb_source, close, outcome, source or "unknown"
    )


def _first_odds_ms(conn: sqlite3.Connection, window_id: int) -> int | None:
    row = conn.execute(
        """
        SELECT recv_ts_ms, up_bid, up_ask, down_bid, down_ask
        FROM odds_ticks
        WHERE window_id = ?
        ORDER BY recv_ts_ms ASC, id ASC
        """,
        (window_id,),
    ).fetchall()
    for item in row:
        if _is_placeholder_book(item):
            continue
        if item["up_ask"] is None and item["down_ask"] is None:
            continue
        return int(item["recv_ts_ms"])
    return None


def _twap_near_end(
    conn: sqlite3.Connection, window_id: int, window_end: int
) -> tuple[str | None, str | None]:
    end_ms = window_end * 1000
    lo = end_ms - TWAP_NEAR_END_BEFORE_MS
    hi = end_ms + TWAP_NEAR_END_AFTER_MS
    row = conn.execute(
        """
        SELECT value FROM twap_ticks
        WHERE window_id = ? AND recv_ts_ms BETWEEN ? AND ?
        ORDER BY ABS(recv_ts_ms - ?) ASC, id DESC
        LIMIT 1
        """,
        (window_id, lo, hi, end_ms),
    ).fetchone()
    if row:
        return str(row["value"]), "twap"
    row = conn.execute(
        """
        SELECT value FROM twap_ticks
        WHERE window_id = ? AND recv_ts_ms <= ?
        ORDER BY recv_ts_ms DESC, id DESC
        LIMIT 1
        """,
        (window_id, end_ms + END_GRACE_MS),
    ).fetchone()
    if row:
        return str(row["value"]), "twap"
    return None, None


def _spot_at_end(
    conn: sqlite3.Connection,
    window_id: int,
    window_start: int,
    window_end: int,
    source: str,
) -> tuple[str | None, str | None]:
    start_ms = window_start * 1000
    end_ms = window_end * 1000 + END_GRACE_MS
    row = conn.execute(
        """
        SELECT price FROM price_ticks
        WHERE source = ?
          AND recv_ts_ms BETWEEN ? AND ?
          AND (window_id = ? OR window_id IS NULL)
        ORDER BY recv_ts_ms DESC, id DESC
        LIMIT 1
        """,
        (source, start_ms, end_ms, window_id),
    ).fetchone()
    if row:
        return str(row["price"]), source
    return None, None


def _downsample(rows: list[sqlite3.Row], bucket_ms: int = PRICE_BUCKET_MS) -> list[sqlite3.Row]:
    if not rows:
        return []
    out: list[sqlite3.Row] = []
    last_bucket: int | None = None
    pending: sqlite3.Row | None = None
    for row in rows:
        bucket = int(row["recv_ts_ms"]) // bucket_ms
        if last_bucket is not None and bucket != last_bucket and pending is not None:
            out.append(pending)
        pending = row
        last_bucket = bucket
    if pending is not None:
        out.append(pending)
    return out


def iter_snapshots(conn: sqlite3.Connection, tape: WindowTape) -> Iterator[Snapshot]:
    start_ms = tape.window_start * 1000
    end_ms = tape.window_end * 1000 + END_GRACE_MS
    odds = [
        row
        for row in conn.execute(
            """
            SELECT recv_ts_ms, up_bid, up_ask, up_mid, down_bid, down_ask, down_mid
            FROM odds_ticks
            WHERE window_id = ?
            ORDER BY recv_ts_ms ASC, id ASC
            """,
            (tape.window_id,),
        ).fetchall()
        if not _is_placeholder_book(row)
    ]
    prices_by_source: dict[str, list[sqlite3.Row]] = {}
    for source in ("binance_spot", "coinbase_spot", "bybit_spot", "binance_futures"):
        raw = conn.execute(
            """
            SELECT recv_ts_ms, source, price
            FROM price_ticks
            WHERE source = ?
              AND recv_ts_ms BETWEEN ? AND ?
              AND (window_id = ? OR window_id IS NULL)
            ORDER BY recv_ts_ms ASC, id ASC
            """,
            (source, start_ms, end_ms, tape.window_id),
        ).fetchall()
        prices_by_source[source] = _downsample(raw)

    twaps = conn.execute(
        """
        SELECT recv_ts_ms, value
        FROM twap_ticks
        WHERE window_id = ?
        ORDER BY recv_ts_ms ASC, id ASC
        """,
        (tape.window_id,),
    ).fetchall()
    volumes = conn.execute(
        """
        SELECT recv_ts_ms, source, base_volume
        FROM candle_volume
        WHERE window_id = ? AND base_volume IS NOT NULL
        ORDER BY recv_ts_ms ASC, id ASC
        """,
        (tape.window_id,),
    ).fetchall()

    events: list[tuple[int, int, str, sqlite3.Row]] = []
    rank = {"price": 0, "twap": 1, "volume": 2, "odds": 3}
    for source_rows in prices_by_source.values():
        for row in source_rows:
            events.append((int(row["recv_ts_ms"]), rank["price"], "price", row))
    for row in twaps:
        events.append((int(row["recv_ts_ms"]), rank["twap"], "twap", row))
    for row in volumes:
        events.append((int(row["recv_ts_ms"]), rank["volume"], "volume", row))
    for row in odds:
        events.append((int(row["recv_ts_ms"]), rank["odds"], "odds", row))
    events.sort(key=lambda item: (item[0], item[1]))

    snap = Snapshot(
        ts_ms=start_ms,
        elapsed_s=0.0,
        seconds_left=float(tape.window_end - tape.window_start),
        slug=tape.slug,
        window_id=tape.window_id,
        ptb=float(tape.ptb),
    )
    spots: dict[str, float | None] = {
        "binance_spot": None,
        "coinbase_spot": None,
        "bybit_spot": None,
        "binance_futures": None,
    }

    def emit() -> Snapshot:
        btc = spots["binance_spot"] or spots["coinbase_spot"] or spots["bybit_spot"]
        return Snapshot(
            ts_ms=snap.ts_ms,
            elapsed_s=snap.elapsed_s,
            seconds_left=snap.seconds_left,
            slug=snap.slug,
            window_id=snap.window_id,
            ptb=snap.ptb,
            btc=btc,
            twap=snap.twap,
            volume_base=snap.volume_base,
            volume_coinbase=snap.volume_coinbase,
            up_bid=snap.up_bid,
            up_ask=snap.up_ask,
            up_mid=snap.up_mid,
            down_bid=snap.down_bid,
            down_ask=snap.down_ask,
            down_mid=snap.down_mid,
            binance_spot=spots["binance_spot"],
            coinbase_spot=spots["coinbase_spot"],
            bybit_spot=spots["bybit_spot"],
            binance_futures=spots["binance_futures"],
        )

    i = 0
    n = len(events)
    while i < n:
        ts = events[i][0]
        while i < n and events[i][0] == ts:
            _, _, kind, row = events[i]
            if kind == "price":
                px = _f(row["price"])
                if px is not None:
                    spots[str(row["source"])] = px
            elif kind == "twap":
                snap.twap = _f(row["value"])
            elif kind == "volume":
                vol = _f(row["base_volume"])
                source = str(row["source"]) if "source" in row.keys() else "binance_spot"
                if source == "coinbase_spot":
                    snap.volume_coinbase = vol
                else:
                    snap.volume_base = vol
            else:
                snap.up_bid = _f(row["up_bid"])
                snap.up_ask = _f(row["up_ask"])
                snap.up_mid = _f(row["up_mid"])
                snap.down_bid = _f(row["down_bid"])
                snap.down_ask = _f(row["down_ask"])
                snap.down_mid = _f(row["down_mid"])
            i += 1
        snap.ts_ms = ts
        snap.elapsed_s = max(0.0, (ts / 1000.0) - tape.window_start)
        snap.seconds_left = max(0.0, tape.window_end - (ts / 1000.0))
        current = emit()
        if current.has_book():
            yield current
