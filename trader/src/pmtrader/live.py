"""Current-window snapshot + entry checks for the UI."""

from __future__ import annotations

import time
from typing import Any

from pmtrader.config import TraderConfig
from pmtrader.snapshot import LiveSnapshot
from pmtrader.strategy import evaluate


def live_payload(trader: Any | None) -> dict[str, Any]:
    if trader is None:
        return {"running": False}

    snap: LiveSnapshot = trader.snap
    cfg: TraderConfig = trader.cfg
    now = time.time()
    decision = evaluate(snap, cfg, now_s=now)
    traded = trader._traded_slug == snap.slug
    left = (snap.window_end - now) if snap.window_end else None
    btc_delta = snap.btc_minus_ptb()
    twap_delta = snap.twap_minus_ptb()
    side = _implied_side(snap, cfg, btc_delta)

    if traded:
        state = "sent"
    elif decision.ok:
        state = "ready"
    else:
        state = f"skip:{decision.reason}"

    armed, from_s, to_s = cfg.watch_span_s(_duration(snap))
    return {
        "running": True,
        "slug": snap.slug,
        "seconds_left": max(0, int(left)) if left is not None else None,
        "state": state,
        "side": decision.side or side,
        "traded": traded,
        "ptb": snap.ptb,
        "btc": snap.btc,
        "btc_delta": btc_delta,
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
            "elapsed_from_min": cfg.elapsed_from_min,
            "elapsed_to_min": cfg.elapsed_to_min,
            "entry_last_minutes": cfg.entry_last_minutes,
            "use_entry_last": cfg.use_entry_last,
            "min_seconds_left": cfg.min_seconds_left,
            "min_btc_away": cfg.min_btc_away,
            "max_btc_away": cfg.max_btc_away,
            "use_btc_distance": cfg.use_btc_distance,
            "use_twap": cfg.use_twap,
            "use_venues": cfg.use_venues,
            "min_venues": cfg.min_venues,
            "stake_usd": cfg.stake_usd,
            "watch_from_s": from_s if armed else 0,
            "watch_to_s": to_s,
        },
        "checks": _checks(snap, cfg, now_s=now, traded=traded, side=side),
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
    in_ask_band = ask is not None and (
        (cfg.odds_min < cfg.odds_max and cfg.odds_min <= ask <= cfg.odds_max)
        or (cfg.odds_min == cfg.odds_max and cfg.odds_min < 0.5 and ask <= cfg.odds_min)
        or (cfg.odds_min == cfg.odds_max and cfg.odds_min >= 0.5 and cfg.odds_min <= ask <= cfg.odds_max)
    )
    crossed = bool(side and snap.mid_entered(side))
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
    btc_label = f"|BTC−PTB| ${cfg.min_btc_away:g}"
    if cfg.max_btc_away is not None:
        btc_label += f"–${cfg.max_btc_away:g}"
    else:
        btc_label += "+"

    return [
        {
            "id": "time",
            "label": f"Elapsed {from_clock}–{to_clock}",
            "ok": in_window and in_elapsed and not_late,
            "value": f"{int(left)}s left" if left is not None else "no window",
            "enabled": True,
        },
        {
            "id": "ptb",
            "label": "Price to beat",
            "ok": has_ptb,
            "value": _num(snap.ptb, 2),
            "enabled": True,
        },
        {
            "id": "btc",
            "label": btc_label,
            "ok": btc_ok,
            "value": _signed(btc_delta, 1),
            "enabled": cfg.use_btc_distance,
        },
        {
            "id": "odds",
            "label": f"Ask fillable {cfg.odds_min:.2f}–{cfg.odds_max:.2f}",
            "ok": in_ask_band,
            "value": _odds_pair(snap.up_ask, snap.down_ask, side, prefix="ask"),
            "enabled": True,
        },
        {
            "id": "cross",
            "label": f"Mid entered {cfg.odds_min:.2f}–{cfg.odds_max:.2f}",
            "ok": crossed,
            "value": _odds_pair(snap.up_mid, snap.down_mid, side, prefix="mid")
            if mid is not None or snap.up_mid is not None
            else ("yes" if crossed else "waiting"),
            "enabled": True,
        },
        {
            "id": "twap",
            "label": "TWAP agrees",
            "ok": twap_ok,
            "value": _signed(twap_delta, 1),
            "enabled": cfg.use_twap,
        },
        {
            "id": "venues",
            "label": f"Venues ≥ {cfg.min_venues}",
            "ok": venues_ok,
            "value": f"{venues}" if side else "—",
            "enabled": cfg.use_venues,
        },
        {
            "id": "once",
            "label": "This window",
            "ok": not traded,
            "value": "already sent" if traded else "open",
            "enabled": True,
        },
    ]


def _clock(seconds: float) -> str:
    total = max(0, int(round(seconds)))
    return f"{total // 60}:{total % 60:02d}"


def _num(value: float | None, digits: int) -> str:
    if value is None:
        return "—"
    return f"{value:,.{digits}f}"


def _signed(value: float | None, digits: int) -> str:
    if value is None:
        return "—"
    return f"{value:+,.{digits}f}"


def _odds_pair(
    up: float | None,
    down: float | None,
    side: str | None,
    *,
    prefix: str = "",
) -> str:
    def fmt(v: float | None) -> str:
        return f"{v:.2f}" if v is not None else "—"

    mark_up = "UP" if side == "up" else "up"
    mark_down = "DOWN" if side == "down" else "down"
    body = f"{mark_up} {fmt(up)} · {mark_down} {fmt(down)}"
    return f"{prefix} {body}".strip() if prefix else body
