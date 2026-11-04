"""Compact JSONL: one entry per fill, one reject per failed FAK, one resolve per settle."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from pmtrader.fees import taker_fee
from pmtrader.orders import describe_clob_error

_ORDER_ID_RE = re.compile(r"0x[0-9a-fA-F]{16,}")


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
    btc_delta = snap.btc_minus_ptb(now_s)
    row["btc_minus_ptb"] = round(btc_delta, 2) if btc_delta is not None else None
    row["btc_source"] = snap.btc_source
    row["spot_deltas"] = snap.spot_deltas(now_s)
    return row


def unmatched_entry_record(
    *,
    snap: Any,
    side: str,
    limit: float,
    stake_usd: float,
    result: Any,
    now_s: float,
    error: str | None,
    error_short: str,
) -> dict[str, Any]:
    """Log a no-match FAK as a paper fill so settlement can score would-have P/L."""
    fill_price = float(getattr(result, "limit", None) or limit or 0)
    stake = float(getattr(result, "stake_usd", None) or stake_usd or 0)
    fill_shares = round(stake / fill_price, 4) if fill_price else 0.0
    row = {
        "event": "entry",
        "unmatched": True,
        "order_id": f"{snap.slug}:miss:{int(now_s)}",
        "slug": snap.slug,
        "side": side,
        "ts": int(now_s),
        "window_end_ts": int(snap.window_end),
        "stake_usd": round(stake, 4) if stake else stake_usd,
        "fill_price": fill_price,
        "fill_shares": fill_shares,
        "fee_usd": round(taker_fee(fill_shares, fill_price), 5),
        "error": error,
        "error_short": error_short or "no match",
    }
    if getattr(result, "dry_run", False):
        row["dry_run"] = True
    btc_delta = snap.btc_minus_ptb(now_s)
    row["btc_minus_ptb"] = round(btc_delta, 2) if btc_delta is not None else None
    row["btc_source"] = snap.btc_source
    row["spot_deltas"] = snap.spot_deltas(now_s)
    return row


def is_no_match_row(row: dict[str, Any]) -> bool:
    if row.get("unmatched"):
        return True
    short = str(row.get("error_short") or "").lower()
    if short == "no match":
        return True
    blob = f"{row.get('error') or ''} {short}".lower()
    return "no match" in blob or "no orders found" in blob


def reject_record(
    *,
    snap: Any,
    side: str,
    limit: float,
    stake_usd: float,
    result: Any,
    now_s: float,
) -> dict[str, Any]:
    short, detail = describe_clob_error(result.error)
    order_id = _reject_order_id(result, snap.slug, now_s)
    row: dict[str, Any] = {
        "event": "reject",
        "order_id": order_id,
        "slug": snap.slug,
        "side": side,
        "ts": int(now_s),
        "window_end_ts": int(snap.window_end),
        "stake_usd": stake_usd,
        "limit": limit,
        "fill_price": None,
        "fill_shares": 0,
        "fee_usd": 0,
        "ok": False,
        "error": detail,
        "error_short": short,
    }
    if result.dry_run:
        row["dry_run"] = True
    btc_delta = snap.btc_minus_ptb(now_s)
    row["btc_minus_ptb"] = round(btc_delta, 2) if btc_delta is not None else None
    row["btc_source"] = snap.btc_source
    row["spot_deltas"] = snap.spot_deltas(now_s)
    return row


def _reject_order_id(result: Any, slug: str, now_s: float) -> str:
    resp = result.response if isinstance(getattr(result, "response", None), dict) else {}
    order_id = str(resp.get("orderID") or resp.get("order_id") or resp.get("id") or "")
    if order_id:
        return order_id
    match = _ORDER_ID_RE.search(str(getattr(result, "error", "") or ""))
    if match:
        return match.group(0)
    return f"{slug}:reject:{int(now_s)}"


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
    row: dict[str, Any] = {
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
    if entry.get("unmatched") or is_no_match_row(entry):
        row["unmatched"] = True
        row["error_short"] = entry.get("error_short") or "no match"
    return row


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
        if ev in {"entry", "resolve", "reject"}:
            out.append(row)
            continue
        if ev == "order" and row.get("ok"):
            out.append(_order_as_entry(row))
    return out


def unresolved_entries(path: Path) -> list[dict[str, Any]]:
    records = analyzer_records(path)
    resolved = {r.get("order_id") for r in records if r.get("event") == "resolve"}
    out: list[dict[str, Any]] = []
    for row in records:
        oid = row.get("order_id")
        if not oid or oid in resolved:
            continue
        ev = row.get("event")
        if ev == "entry" and (row.get("unmatched") or not row.get("error")):
            out.append(_as_settleable(row))
        elif ev == "reject" and is_no_match_row(row):
            out.append(_as_settleable(row))
    return out


def _as_settleable(row: dict[str, Any]) -> dict[str, Any]:
    if not (row.get("unmatched") or row.get("event") == "reject"):
        return row
    out = dict(row)
    out["unmatched"] = True
    limit = float(out.get("fill_price") or out.get("limit") or 0)
    stake = float(out.get("stake_usd") or 0)
    shares = float(out.get("fill_shares") or 0)
    if limit and (not shares):
        shares = round(stake / limit, 4) if stake else 0.0
        out["fill_price"] = limit
        out["fill_shares"] = shares
    if not out.get("fee_usd") and shares and limit:
        out["fee_usd"] = round(taker_fee(shares, limit), 5)
    return out


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
