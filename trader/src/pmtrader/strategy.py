"""Combo entry filters: a per-sample port of Strategy Lab's ``findEntry``.

``evaluate`` is called once per second (see ``Trader._sample_loop``); each call
is one tape row ``t`` of lab_engine.js. The side must *enter* the odds band on
this very sample (previous sample outside, this one inside), every enabled
filter must agree on the same sample, and the fill is this sample's ask.
Venue and TWAP prints older than ``PRICE_STALE_AFTER_S`` are treated as missing
so a dead socket cannot be paired with a live book.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from pmtrader.config import TraderConfig
from pmtrader.snapshot import LiveSnapshot

logger = logging.getLogger(__name__)

Side = str  # "up" | "down"


@dataclass(frozen=True, slots=True)
class Decision:
    side: Side | None
    reason: str
    ask: float | None
    limit: float
    inverted: bool = False

    @property
    def ok(self) -> bool:
        return self.side is not None and self.reason == "ok"


def evaluate(snap: LiveSnapshot, cfg: TraderConfig, *, now_s: float, day: Any | None = None) -> Decision:
    cap = cfg.effective_fak_limit()
    if snap.window_end <= 0 or snap.window_start <= 0:
        snap.clear_odds_memory()
        return Decision(None, "no_window", None, cap)

    duration = max(1.0, float(snap.window_end - snap.window_start))
    t = round(now_s - snap.window_start)
    _, from_s, to_s = cfg.watch_span_s(duration)
    if snap.window_end - now_s < cfg.min_seconds_left:
        snap.clear_odds_memory()
        return Decision(None, "too_late", None, cap)
    if not cfg.when_ok(snap.window_start):
        snap.clear_odds_memory()
        return Decision(None, "outside_when", None, cap)
    if day is not None:
        reason = day.entry_block_reason(snap.window_start)
        if reason:
            snap.clear_odds_memory()
            return Decision(None, str(reason), None, cap)
    if not round(from_s) <= t <= round(to_s):
        snap.clear_odds_memory()
        return Decision(None, "outside_elapsed", None, cap)

    if not snap.in_watch:
        snap.clear_odds_memory()
        snap.in_watch = True

    def skip(reason: str, ask: float | None = None) -> Decision:
        snap.remember_mids()
        return Decision(None, reason, ask, cap)

    if snap.ptb is None:
        return skip("no_ptb")

    lo, hi = cfg.odds_min, cfg.odds_max
    up = snap.mid_for("up")
    down = snap.mid_for("down")
    side: Side
    if cfg.use_btc_distance:
        delta = snap.btc_minus_ptb(now_s)
        if delta is None:
            return skip("no_btc")
        if not _distance_ok(abs(delta), cfg):
            return skip("btc_out")
        side = "up" if delta > 0 else "down"
        curr = up if side == "up" else down
        prev = snap.prev_up_mid if side == "up" else snap.prev_down_mid
        if not _entered_band(prev, curr, lo, hi):
            return skip("odds_out", snap.ask_for(side))
    else:
        picked = _pick_entered_side(snap.prev_up_mid, up, snap.prev_down_mid, down, lo, hi)
        if picked is None:
            return skip("odds_out", snap.up_ask if snap.up_ask is not None else snap.down_ask)
        side = picked

    ask = snap.ask_for(side)
    if cfg.use_twap:
        twap_delta = snap.twap_minus_ptb(now_s)
        if twap_delta is None:
            return skip("no_twap", ask)
        if not (twap_delta > 0 if side == "up" else twap_delta < 0):
            return skip("twap_disagree", ask)
    if cfg.use_venues and snap.venues_on_side(side, now_s) < cfg.min_venues:
        return skip("venues", ask)

    if ask is None or not 0 < ask < 1:
        return skip("no_ask", ask)
    if ask > cap:
        return skip("ask_above_cap", ask)

    # seconds_since_window_open = now - (window_end - 300). A full signal inside
    # the delay closes the window. The old fill-time result was about signals
    # that first crossed after 10s, so this cross is not released later.
    elapsed = now_s - (float(snap.window_end) - 300.0)
    if cfg.min_delay_seconds > 0 and (snap.early_cross or elapsed <= cfg.min_delay_seconds):
        snap.early_cross = True
        snap.remember_mids()
        reason = "early_cross" if elapsed > cfg.min_delay_seconds else "too_early"
        _log_signal(snap, elapsed, f"skipped {reason}")
        return Decision(None, reason, ask, cap)

    atr_reason = _atr_block_reason(snap, cfg)
    if atr_reason is not None:
        snap.remember_mids()
        _log_signal(snap, elapsed, f"skipped {atr_reason}")
        return Decision(None, atr_reason, ask, cap)

    snap.remember_mids()
    order_side = side
    inverted = cfg.hour_is_inverted(snap.window_start)
    if inverted:
        order_side = "down" if side == "up" else "up"
        opposite = snap.ask_for(order_side)
        if opposite is None or not (0 < opposite <= cap):
            _log_signal(snap, elapsed, "skipped invert_ask")
            return Decision(None, "invert_ask", opposite, cap, inverted=True)
        ask = opposite
    _log_signal(snap, elapsed, "traded inverted" if inverted else "traded")
    # Marketable FAK: one tick above the observed ask, never above the cap.
    limit = min(ask + float(cfg.tick_size), cap)
    return Decision(order_side, "ok", ask, limit, inverted=inverted)


def _atr_block_reason(snap: LiveSnapshot, cfg: TraderConfig) -> str | None:
    if snap.atr_block:
        return snap.atr_block
    if snap.atr_ratio is None or snap.atr_value is None or snap.atr_mean is None:
        return "atr_missing"
    if snap.atr_ratio > cfg.atr_mean_max:
        return "atr_high"
    return None


def _fmt(value: float | None) -> str:
    return "-" if value is None else f"{value:.6f}"


def _log_signal(snap: LiveSnapshot, elapsed: float, decision: str) -> None:
    window_id = snap.slug if snap.slug and snap.slug != "-" else snap.window_start
    logger.info(
        "signal window=%s seconds_since_window_open=%.3f ATR=%s ATR_mean=%s ratio=%s decision=%s",
        window_id,
        elapsed,
        _fmt(snap.atr_value),
        _fmt(snap.atr_mean),
        _fmt(snap.atr_ratio),
        decision,
    )


def _distance_ok(abs_dist: float, cfg: TraderConfig) -> bool:
    if abs_dist < cfg.min_btc_away:
        return False
    if cfg.max_btc_away is not None and abs_dist > cfg.max_btc_away:
        return False
    return True


def _in_band(value: float | None, lo: float, hi: float) -> bool:
    if value is None:
        return False
    return lo <= value <= hi


def _entered_band(prev: float | None, curr: float | None, lo: float, hi: float) -> bool:
    if curr is None or prev is None:
        return False
    if lo < hi:
        return (not _in_band(prev, lo, hi)) and _in_band(curr, lo, hi)
    return (prev < lo <= curr) or (prev > lo >= curr)


def _pick_entered_side(
    prev_up: float | None,
    up: float | None,
    prev_down: float | None,
    down: float | None,
    lo: float,
    hi: float,
) -> Side | None:
    up_in = _entered_band(prev_up, up, lo, hi)
    down_in = _entered_band(prev_down, down, lo, hi)
    if up_in and down_in:
        mid = (lo + hi) / 2
        up_dist = abs((up if up is not None else mid) - mid)
        down_dist = abs((down if down is not None else mid) - mid)
        return "up" if up_dist <= down_dist else "down"
    if up_in:
        return "up"
    if down_in:
        return "down"
    return None


def _ask_fillable(ask: float, cfg: TraderConfig) -> bool:
    """A live FAK needs a real ask at or below the cap; the Lab only needs 0 < ask < 1."""
    return 0 < ask < 1 and ask <= cfg.effective_fak_limit()
