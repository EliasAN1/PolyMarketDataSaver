"""Window winner: same rules as the live trader.

The trader resolves UP/DOWN from post-close CLOB prices (one side near $1,
the other near $0), then falls back to Gamma. The Lab and collector use this
same inference so backtests and live fills agree on who won.
"""

from __future__ import annotations

import sqlite3
from typing import Any

WIN_THRESHOLD = 0.95
LOSE_THRESHOLD = 0.10
# Last 15s of the window plus 30s after close — same idea as the trader
# waiting a few seconds after expiry for the book to go to $1 / $0.
CLOB_RESOLVE_BEFORE_MS = 15_000
CLOB_RESOLVE_AFTER_MS = 30_000

OFFICIAL_SOURCES = frozenset({"polymarket", "gamma", "clob_live", "clob_rest", "clob"})


def binary_outcome(final_price: str | None, ptb: str | None) -> str | None:
    if final_price is None or ptb is None:
        return None
    try:
        final = float(final_price)
        beat = float(ptb)
    except (TypeError, ValueError):
        return None
    if final >= beat:
        return "up"
    return "down"


def _f(value: object | None) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _side_price(*, mid: float | None, ask: float | None, bid: float | None) -> float | None:
    if mid is not None:
        return mid
    if ask is not None:
        return ask
    return bid


def infer_outcome_from_clob(
    *,
    up_mid: float | None = None,
    down_mid: float | None = None,
    up_ask: float | None = None,
    down_ask: float | None = None,
    up_bid: float | None = None,
    down_bid: float | None = None,
    win_threshold: float = WIN_THRESHOLD,
    lose_threshold: float = LOSE_THRESHOLD,
) -> str | None:
    """Return the side trading near $1 once the window is over.

    Same thresholds as ``pmtrader.outcome.infer_outcome_from_clob``.
    """
    up = _side_price(mid=up_mid, ask=up_ask, bid=up_bid)
    down = _side_price(mid=down_mid, ask=down_ask, bid=down_bid)
    if up is None or down is None:
        return None
    if up >= win_threshold and down <= lose_threshold:
        return "up"
    if down >= win_threshold and up <= lose_threshold:
        return "down"
    return None


def infer_outcome_from_odds_row(row: Any) -> str | None:
    keys = row.keys() if hasattr(row, "keys") else None

    def get(name: str) -> float | None:
        if keys is not None and name not in keys:
            return None
        try:
            return _f(row[name])
        except (KeyError, IndexError, TypeError):
            return None

    return infer_outcome_from_clob(
        up_mid=get("up_mid"),
        down_mid=get("down_mid"),
        up_ask=get("up_ask"),
        down_ask=get("down_ask"),
        up_bid=get("up_bid"),
        down_bid=get("down_bid"),
    )


def infer_outcome_from_odds_ticks(
    conn: sqlite3.Connection,
    *,
    window_id: int,
    window_end: int,
) -> str | None:
    """Walk recent odds ticks newest-first and apply the trader CLOB rule."""
    end_ms = int(window_end) * 1000
    lo = end_ms - CLOB_RESOLVE_BEFORE_MS
    hi = end_ms + CLOB_RESOLVE_AFTER_MS
    rows = conn.execute(
        """
        SELECT up_bid, up_ask, up_mid, down_bid, down_ask, down_mid
        FROM odds_ticks
        WHERE window_id = ? AND recv_ts_ms BETWEEN ? AND ?
        ORDER BY recv_ts_ms DESC, id DESC
        LIMIT 40
        """,
        (window_id, lo, hi),
    ).fetchall()
    for row in rows:
        outcome = infer_outcome_from_odds_row(row)
        if outcome is not None:
            return outcome
    return None


async def fetch_clob_odds(
    client: Any,
    *,
    up_token_id: str,
    down_token_id: str,
) -> dict[str, float | None]:
    """Top-of-book odds via CLOB REST — same path as the live trader."""
    from pmdsaver.streams.polymarket_clob import (
        CLOB_REST_BOOKS_URL,
        best_level_price,
        compute_mid,
        is_placeholder_book,
    )

    out: dict[str, float | None] = {
        "up_mid": None,
        "down_mid": None,
        "up_ask": None,
        "down_ask": None,
        "up_bid": None,
        "down_bid": None,
    }
    if not up_token_id or not down_token_id:
        return out
    try:
        response = await client.post(
            CLOB_REST_BOOKS_URL,
            json=[{"token_id": up_token_id}, {"token_id": down_token_id}],
        )
        if getattr(response, "status_code", 0) != 200:
            return out
        payload = response.json()
        books = payload if isinstance(payload, list) else [payload]
    except Exception:
        return out

    by_token: dict[str, dict[str, Any]] = {}
    for book in books:
        if not isinstance(book, dict):
            continue
        token_id = str(book.get("asset_id") or book.get("token_id") or book.get("tokenId") or "")
        if token_id:
            by_token[token_id] = book

    for label, token_id in (("up", up_token_id), ("down", down_token_id)):
        book = by_token.get(token_id)
        if not book:
            continue
        bid = best_level_price(book.get("bids") or [], side="bid")
        ask = best_level_price(book.get("asks") or [], side="ask")
        if bid is not None and ask is not None and is_placeholder_book(bid, ask):
            continue
        mid = compute_mid(bid, ask) if bid and ask else None
        out[f"{label}_bid"] = _f(bid)
        out[f"{label}_ask"] = _f(ask)
        out[f"{label}_mid"] = _f(mid)
    return out
