"""Current-window snapshot + entry checks for the UI."""

from __future__ import annotations

import time
from typing import Any

from pmtrader.config import TraderConfig
from pmtrader.snapshot import LiveSnapshot
from pmtrader.strategy import Decision, _ask_fillable

# Skip reasons raised after the odds-band test passed on the last sample.
_PAST_BAND_REASONS = frozenset({"ok", "no_twap", "twap_disagree", "venues", "no_ask", "ask_above_cap"})


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
    left = (snap.window_end - now) if snap.window_end else None
    btc_delta = snap.btc_minus_ptb()
    twap_delta = snap.twap_minus_ptb()
    side = _implied_side(snap, cfg, btc_delta)

    if traded:
        state = "sent"
    elif decision is None:
        state = "waiting"
    elif decision.ok:
        state = "ready"
    else:
        state = f"skip:{decision.reason}"

    armed, from_s, to_s = cfg.watch_span_s(_duration(snap))
    return {
        "running": True,
        "slug": snap.slug,
        "seconds_left": max(0, int(left)) if left is not None else None,
        "elapsed_s": max(0, int(now - snap.window_start)) if snap.window_start else None,
        "state": state,
        "side": (decision.side if decision is not None else None) or side,
        "traded": traded,
        "ptb": snap.ptb,
        "btc": snap.btc,
        "btc_delta": btc_delta,
        "spot_deltas": snap.spot_deltas(),
        "twap": snap.twap,
        "twap_delta": twap_delta,
        "up_ask": snap.up_ask,
        "down_ask": snap.down_ask,
        "up_mid": snap.up_mid,
        "down_mid": snap.down_mid,
        "venues_up": snap.venues_on_side("up"),
        "venues_down": snap.venues_on_side("down"),
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
        },
        "checks": _checks(snap, cfg, now_s=now, traded=traded, side=side, decision=decision),
    }


def _duration(snap: LiveSnapshot) -> float:
    if snap.window_end > snap.window_start:
        return float(snap.window_end - snap.window_start)
    return 300.0


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
    btc_delta = snap.btc_minus_ptb()
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
    twap_delta = snap.twap_minus_ptb()
    twap_ok = (
        not cfg.use_twap
        or (
            twap_delta is not None
            and side is not None
            and ((side == "up" and twap_delta > 0) or (side == "down" and twap_delta < 0))
        )
    )
    venues = snap.venues_on_side(side) if side else 0
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

    return [
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
            "value": f"${_num(snap.ptb, 2)}" if snap.ptb is not None else "-",
            "enabled": True,
        },
        {
            "id": "btc",
            "name": "BTC Distance",
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
            "target": "Directional",
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
            "value": "Sent" if traded else "Ready",
            "enabled": True,
        },
    ]


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
