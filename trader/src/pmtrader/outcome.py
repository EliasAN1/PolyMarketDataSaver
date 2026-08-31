"""Fast UP/DOWN outcome inference from post-close CLOB prices."""

from __future__ import annotations

from typing import Any

import httpx

from pmtrader.streams.polymarket_clob import (
    CLOB_REST_BOOKS_URL,
    best_level_price,
    compute_mid,
    is_placeholder_book,
)

Side = str  # "up" | "down"

WIN_THRESHOLD = 0.95
LOSE_THRESHOLD = 0.10


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
) -> Side | None:
    """Return the side trading near $1 once the window is over.

    After expiry the winning share settles toward $1 and the loser toward $0.
    Requires a clear split: one side >= win_threshold and the other <= lose_threshold.
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


def _book_top(payload: dict[str, Any]) -> tuple[str | None, str | None]:
    bid = best_level_price(payload.get("bids") or [], side="bid")
    ask = best_level_price(payload.get("asks") or [], side="ask")
    if bid is not None and ask is not None and is_placeholder_book(bid, ask):
        return bid, ask
    return bid, ask


async def fetch_clob_odds(
    client: httpx.AsyncClient,
    *,
    up_token_id: str,
    down_token_id: str,
) -> dict[str, float | None]:
    """Fetch top-of-book odds for UP/DOWN tokens via CLOB REST."""
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
        if response.status_code != 200:
            return out
        payload = response.json()
        books = payload if isinstance(payload, list) else [payload]
    except Exception:
        return out

    by_token: dict[str, dict[str, Any]] = {}
    for book_payload in books:
        if not isinstance(book_payload, dict):
            continue
        token_id = str(
            book_payload.get("asset_id")
            or book_payload.get("token_id")
            or book_payload.get("tokenId")
            or ""
        )
        if token_id:
            by_token[token_id] = book_payload

    for label, token_id in (("up", up_token_id), ("down", down_token_id)):
        book = by_token.get(token_id)
        if not book:
            continue
        bid, ask = _book_top(book)
        bid_f = _f(bid)
        ask_f = _f(ask)
        mid_s = compute_mid(bid, ask) if bid and ask else None
        mid_f = _f(mid_s)
        out[f"{label}_bid"] = bid_f
        out[f"{label}_ask"] = ask_f
        out[f"{label}_mid"] = mid_f

    return out
