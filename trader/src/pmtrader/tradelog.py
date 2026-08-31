"""Compact JSONL: one entry per fill, one resolve per settle."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pmtrader.fees import taker_fee


def parse_fill(response: dict[str, Any] | None, *, limit: float, stake_usd: float) -> tuple[str, float, float]:
    resp = response or {}
    order_id = str(resp.get("orderID") or resp.get("order_id") or resp.get("id") or "")
    taking = _f(resp.get("takingAmount"))
    making = _f(resp.get("makingAmount"))
    if taking and taking > 0 and making is not None:
        return order_id, making / taking, taking
    if limit > 0:
        return order_id, limit, stake_usd / limit
    return order_id, limit, 0.0


def entry_record(
    *,
    snap: Any,
    side: str,
    limit: float,
    stake_usd: float,
    result: Any,
    now_s: float,
) -> dict[str, Any]:
    order_id, fill_price, fill_shares = parse_fill(
        result.response if result.response else None,
        limit=limit,
        stake_usd=stake_usd,
    )
    if not order_id:
        order_id = f"{snap.slug}:{int(now_s)}"
    row: dict[str, Any] = {
        "event": "entry",
        "order_id": order_id,
        "slug": snap.slug,
        "side": side,
        "ts": int(now_s),
        "window_end_ts": int(snap.window_end),
        "stake_usd": round(fill_price * fill_shares, 4) if fill_price and fill_shares else stake_usd,
        "fill_price": fill_price,
        "fill_shares": fill_shares,
        "fee_usd": round(taker_fee(fill_shares, fill_price), 5),
    }
    if result.dry_run:
        row["dry_run"] = True
    if result.error:
        row["error"] = result.error
    btc_delta = snap.btc_minus_ptb()
    row["btc_minus_ptb"] = round(btc_delta, 2) if btc_delta is not None else None
    row["spot_deltas"] = snap.spot_deltas()
    return row


def resolve_record(entry: dict[str, Any], *, outcome: str, now_s: float) -> dict[str, Any]:
    side = str(entry.get("side") or "")
    won = outcome == side
    stake = float(entry.get("stake_usd") or entry.get("requested_stake_usd") or 0)
    fill_price = float(entry.get("fill_price") or 0)
    fill_shares = float(entry.get("fill_shares") or 0)
    cost = (fill_price * fill_shares) if fill_price and fill_shares else stake
    fee = float(entry.get("fee_usd") or 0)
    if fee <= 0 and fill_price and fill_shares:
        fee = taker_fee(fill_shares, fill_price)
    payout = fill_shares if won else 0.0
    gross = payout - cost
    return {
        "event": "resolve",
        "order_id": entry.get("order_id"),
        "slug": entry.get("slug"),
        "side": side,
        "ts": int(now_s),
        "won": won,
        "outcome": outcome,
        "fee_usd": round(fee, 5),
        "gross_pnl_usd": round(gross, 4),
        "total_fees_usd": round(fee, 5),
        "net_pnl_usd": round(gross - fee, 4),
    }


def read_records(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def analyzer_records(path: Path) -> list[dict[str, Any]]:
    """Read entry/resolve rows, including older analyzer-shaped lines."""
    out: list[dict[str, Any]] = []
    for row in read_records(path):
        ev = row.get("event")
        if ev in {"entry", "resolve"}:
            out.append(row)
            continue
        if ev == "order" and row.get("ok"):
            out.append(_order_as_entry(row))
    return out


def unresolved_entries(path: Path) -> list[dict[str, Any]]:
    records = analyzer_records(path)
    resolved = {r.get("order_id") for r in records if r.get("event") == "resolve"}
    return [
        r
        for r in records
        if r.get("event") == "entry" and r.get("order_id") and r.get("order_id") not in resolved
    ]


def _order_as_entry(row: dict[str, Any]) -> dict[str, Any]:
    ts_ms = int(row.get("ts_ms") or 0)
    ts = int(row.get("ts") or (ts_ms // 1000 if ts_ms > 10_000_000_000 else ts_ms) or 0)
    resp = row.get("response") if isinstance(row.get("response"), dict) else {}
    order_id, fill_price, fill_shares = parse_fill(
        resp,
        limit=float(row.get("limit") or 0),
        stake_usd=float(row.get("stake_usd") or 0),
    )
    if not order_id:
        order_id = f"{row.get('slug')}:{ts}"
    return {
        "event": "entry",
        "order_id": order_id,
        "slug": row.get("slug"),
        "side": row.get("side"),
        "ts": ts,
        "window_end_ts": row.get("window_end_ts"),
        "stake_usd": row.get("stake_usd"),
        "fill_price": fill_price,
        "fill_shares": fill_shares,
        "fee_usd": round(taker_fee(fill_shares, fill_price), 5),
    }


def _f(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
