"""Current-window snapshot + entry checks for the UI."""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any

from pmtrader.clock import current_window
from pmtrader.config import TraderConfig
from pmtrader.snapshot import LiveSnapshot
from pmtrader.strategy import Decision, _ask_fillable
from pmtrader.when import WEEKDAY_NAMES, session_catalog

# Skip reasons raised after the odds-band test passed on the last sample.
_PAST_BAND_REASONS = frozenset({"ok", "no_twap", "twap_disagree", "venues", "no_ask", "ask_above_cap"})

_VENUE_NAMES = {
    "binance_spot": "Binance",
    "coinbase_spot": "Coinbase",
    "bybit_spot": "Bybit",
    "median": "Median",
}


def _venue_name(cfg: TraderConfig) -> str:
    return _VENUE_NAMES.get(cfg.btc_source, "Binance")


def live_payload(trader: Any | None) -> dict[str, Any]:
    if trader is None:
        return {"running": False}

    snap: LiveSnapshot = trader.snap
    cfg: TraderConfig = trader.cfg
    now = time.time()
    # The runner samples once per second and owns the band memory; re-running
    # evaluate() here would mutate it from the UI thread.
    decision: Decision | None = trader._last_decision
    traded = trader._traded_slug == snap.slug
    order = _order_for_window(trader, snap.slug)
    left = (snap.window_end - now) if snap.window_end else None
    btc_delta = snap.btc_minus_ptb(now)
    twap_delta = snap.twap_minus_ptb(now)
    side = _implied_side(snap, cfg, btc_delta)

    clock = current_window()
    expired = bool(snap.window_end and now >= snap.window_end)
    stale = expired and bool(snap.slug) and snap.slug != "-" and snap.slug != clock.slug

    if stale:
        state = "rolling"
    elif traded and order is not None and not order.get("ok"):
        state = "failed"
    elif traded:
        state = "sent"
    elif decision is None:
        state = "waiting"
    elif decision.ok:
        state = "ready"
    else:
        state = f"skip:{decision.reason}"

    armed, from_s, to_s = cfg.watch_span_s(_duration(snap))
    ptb_wait = None if snap.ptb is not None else _ptb_wait_reason(trader, now)
    return {
        "running": True,
        "slug": snap.slug,
        "seconds_left": max(0, int(left)) if left is not None else None,
        "expired": expired,
        "stale": stale,
        "elapsed_s": max(0, int(now - snap.window_start)) if snap.window_start else None,
        "state": state,
        "side": (decision.side if decision is not None else None) or (order.get("side") if order else None) or side,
        "traded": traded,
        "order": order,
        "ptb": snap.ptb,
        "ptb_wait": ptb_wait,
        "btc": snap.btc_at(now),
        "btc_delta": btc_delta,
        "spot_deltas": snap.spot_deltas(now),
        "twap": snap.twap_at(now),
        "twap_delta": twap_delta,
        "up_ask": snap.up_ask,
        "down_ask": snap.down_ask,
        "up_mid": snap.up_mid,
        "down_mid": snap.down_mid,
        "venues_up": snap.venues_on_side("up", now),
        "venues_down": snap.venues_on_side("down", now),
        "config": {
            "odds_min": cfg.odds_min,
            "odds_max": cfg.odds_max,
            "fak_limit": cfg.effective_fak_limit(),
            "trigger_band": cfg.trigger_band_label(),
            "fillable_ask": cfg.fillable_ask_label(),
            "elapsed_from_min": cfg.elapsed_from_min,
            "elapsed_to_min": cfg.elapsed_to_min,
            "entry_last_minutes": cfg.entry_last_minutes,
            "use_entry_last": cfg.use_entry_last,
            "min_seconds_left": cfg.min_seconds_left,
            "min_btc_away": cfg.min_btc_away,
            "max_btc_away": cfg.max_btc_away,
            "btc_source": cfg.btc_source,
            "use_btc_distance": cfg.use_btc_distance,
            "use_twap": cfg.use_twap,
            "use_venues": cfg.use_venues,
            "min_venues": cfg.min_venues,
            "stake_usd": cfg.stake_usd,
            "watch_from_s": from_s if armed else 0,
            "watch_to_s": to_s,
            **cfg.when_payload(),
        },
        "when": {
            **cfg.when_payload(),
            "ok": cfg.when_ok(snap.window_start or now),
            "catalog": session_catalog(),
        },
        "checks": _checks(
            snap, cfg, now_s=now, traded=traded, side=side, decision=decision, ptb_wait=ptb_wait, order=order
        ),
    }


def _order_for_window(trader: Any, slug: str | None) -> dict[str, Any] | None:
    order = getattr(trader, "_last_order", None)
    if not isinstance(order, dict):
        return None
    if slug and order.get("slug") and order.get("slug") != slug:
        return None
    return {
        "ok": bool(order.get("ok")),
        "side": order.get("side"),
        "limit": order.get("limit"),
        "dry_run": bool(order.get("dry_run")),
        "error": order.get("error"),
        "error_short": order.get("error_short"),
    }


def _duration(snap: LiveSnapshot) -> float:
    if snap.window_end > snap.window_start:
        return float(snap.window_end - snap.window_start)
    return 300.0


def _ptb_wait_reason(trader: Any, now_s: float) -> str:
    rtds = getattr(trader, "rtds", None)
    start = getattr(trader.snap, "window_start", 0) or 0
    if rtds is not None and hasattr(rtds, "wait_reason"):
        return str(rtds.wait_reason(start, now_s))
    return "waiting for RTDS / Gamma"


def _implied_side(snap: LiveSnapshot, cfg: TraderConfig, btc_delta: float | None) -> str | None:
    if cfg.use_btc_distance:
        if btc_delta is None:
            return None
        return "up" if btc_delta > 0 else "down"
    return None


def _checks(
    snap: LiveSnapshot,
    cfg: TraderConfig,
    *,
    now_s: float,
    traded: bool,
    side: str | None,
    decision: Decision | None,
    ptb_wait: str | None = None,
    order: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    left = (snap.window_end - now_s) if snap.window_end else None
    elapsed = (now_s - snap.window_start) if snap.window_start else None
    in_window = snap.window_end > 0
    not_late = left is not None and left >= cfg.min_seconds_left
    armed, from_s, to_s = cfg.watch_span_s(_duration(snap))
    in_elapsed = (not armed) or (
        elapsed is not None and from_s <= elapsed <= to_s
    )
    has_ptb = snap.ptb is not None
    btc_delta = snap.btc_minus_ptb(now_s)
    abs_d = None if btc_delta is None else abs(btc_delta)
    btc_ok = (not cfg.use_btc_distance) or (
        abs_d is not None
        and abs_d >= cfg.min_btc_away
        and (cfg.max_btc_away is None or abs_d <= cfg.max_btc_away)
    )
    ask = snap.ask_for(side) if side else None
    mid = snap.mid_for(side) if side else None
    in_ask_band = ask is not None and _ask_fillable(ask, cfg)
    crossed = decision is not None and decision.reason in _PAST_BAND_REASONS
    twap_delta = snap.twap_minus_ptb(now_s)
    twap_ok = (
        not cfg.use_twap
        or (
            twap_delta is not None
            and side is not None
            and ((side == "up" and twap_delta > 0) or (side == "down" and twap_delta < 0))
        )
    )
    venues = snap.venues_on_side(side, now_s) if side else 0
    venues_ok = (not cfg.use_venues) or venues >= cfg.min_venues
    from_clock = _clock(from_s)
    to_clock = _clock(to_s)

    # Concise target string
    if cfg.min_btc_away == 0 and cfg.max_btc_away is not None:
        btc_target = f"<= ${cfg.max_btc_away:g}"
    elif cfg.min_btc_away > 0 and cfg.max_btc_away is not None:
        btc_target = f"${cfg.min_btc_away:g}-${cfg.max_btc_away:g}"
    elif cfg.min_btc_away > 0 and cfg.max_btc_away is None:
        btc_target = f">= ${cfg.min_btc_away:g}"
    else:
        btc_target = "Active"

    # Clean, concise odds values
    if side and ask is not None:
        odds_val = f"{side.upper()} {ask:.2f}"
    else:
        odds_val = _odds_pair(snap.up_ask, snap.down_ask, side)

    if side and mid is not None:
        cross_val = f"{side.upper()} {mid:.2f}"
    else:
        cross_val = "Waiting" if not crossed else "Triggered"

    when_ok = (not cfg.when_filter_on()) or cfg.when_ok(snap.window_start or now_s)
    when_now = ""
    if snap.window_start:
        opened = datetime.fromtimestamp(snap.window_start)
        when_now = f"{WEEKDAY_NAMES[int(opened.strftime('%w'))]} {opened.strftime('%H:%M')}"
    else:
        when_now = "No window"

    return [
        {
            "id": "when",
            "name": "Hours / markets",
            "target": cfg.when_label(),
            "ok": when_ok,
            "value": when_now,
            "enabled": True,
        },
        {
            "id": "time",
            "name": "Elapsed Window",
            "target": f"{from_clock}-{to_clock}",
            "ok": in_window and in_elapsed and not_late,
            "value": (
                f"{_clock(elapsed)} into window"
                if elapsed is not None
                else "No window"
            ),
            "enabled": True,
        },
        {
            "id": "ptb",
            "name": "Price To Beat",
            "target": "PTB Baseline",
            "ok": has_ptb,
            "value": f"${_num(snap.ptb, 2)}" if snap.ptb is not None else (ptb_wait or "waiting"),
            "enabled": True,
        },
        {
            "id": "btc",
            "name": f"{_venue_name(cfg)} vs PTB",
            "target": btc_target,
            "ok": btc_ok,
            "value": _signed(btc_delta, 1),
            "enabled": cfg.use_btc_distance,
        },
        {
            "id": "odds",
            "name": "Fillable Ask",
            "target": cfg.fillable_ask_label(),
            "ok": in_ask_band,
            "value": odds_val,
            "enabled": True,
        },
        {
            "id": "cross",
            "name": "Mid Cross Trigger",
            "target": cfg.trigger_band_label(),
            "ok": crossed,
            "value": cross_val,
            "enabled": True,
        },
        {
            "id": "twap",
            "name": "TWAP Agrees",
            "target": f"Same side as {_venue_name(cfg)}",
            "ok": twap_ok,
            "value": _signed(twap_delta, 1),
            "enabled": cfg.use_twap,
        },
        {
            "id": "venues",
            "name": "Venues Consensus",
            "target": f">= {cfg.min_venues} venues",
            "ok": venues_ok,
            "value": f"{venues} agreed" if side else "Waiting",
            "enabled": cfg.use_venues,
        },
        {
            "id": "once",
            "name": "Window Order Lock",
            "target": "1 per 5m",
            "ok": not traded,
            "value": _once_value(traded, order),
            "enabled": True,
        },
    ]


def _once_value(traded: bool, order: dict[str, Any] | None) -> str:
    if not traded:
        return "Ready"
    if order and not order.get("ok"):
        short = str(order.get("error_short") or "rejected")
        side = str(order.get("side") or "").upper()
        return f"Failed: {side} {short}".strip()
    if order and order.get("side"):
        return f"Sent {str(order['side']).upper()}"
    return "Sent"


def _clock(seconds: float) -> str:
    total = max(0, int(round(seconds)))
    return f"{total // 60}:{total % 60:02d}"


def _num(value: float | None, digits: int) -> str:
    if value is None:
        return "-"
    return f"{value:,.{digits}f}"


def _signed(value: float | None, digits: int) -> str:
    if value is None:
        return "-"
    sign = "+" if value >= 0 else "-"
    return f"{sign}${abs(value):,.{digits}f}"


def _odds_pair(
    up: float | None,
    down: float | None,
    side: str | None,
) -> str:
    def fmt(v: float | None) -> str:
        return f"{v:.2f}" if v is not None else "-"

    if side == "up":
        return f"UP {fmt(up)}"
    if side == "down":
        return f"DN {fmt(down)}"
    return f"{fmt(up)} / {fmt(down)}"
