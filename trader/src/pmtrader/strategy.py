"""Combo entry filters aligned with Strategy Lab."""

from __future__ import annotations

from dataclasses import dataclass

from pmtrader.config import TraderConfig
from pmtrader.snapshot import LiveSnapshot

Side = str  # "up" | "down"


@dataclass(frozen=True, slots=True)
class Decision:
    side: Side | None
    reason: str
    ask: float | None
    limit: float

    @property
    def ok(self) -> bool:
        return self.side is not None and self.reason == "ok"


def evaluate(snap: LiveSnapshot, cfg: TraderConfig, *, now_s: float) -> Decision:
    limit = cfg.effective_fak_limit()
    if snap.window_end <= 0 or snap.window_start <= 0:
        snap.clear_odds_memory()
        return Decision(None, "no_window", None, limit)

    duration = max(1.0, float(snap.window_end - snap.window_start))
    elapsed = now_s - snap.window_start
    seconds_left = snap.window_end - now_s
    armed, from_s, to_s = cfg.watch_span_s(duration)
    in_watch = (not armed) or (from_s <= elapsed <= to_s)

    if seconds_left < cfg.min_seconds_left:
        snap.clear_odds_memory()
        return Decision(None, "too_late", None, limit)
    if not in_watch:
        snap.clear_odds_memory()
        return Decision(None, "outside_elapsed", None, limit)

    if not snap.in_watch:
        snap.clear_odds_memory()
        snap.in_watch = True

    if snap.ptb is None:
        snap.remember_mids()
        return Decision(None, "no_ptb", None, limit)

    lo, hi = cfg.odds_min, cfg.odds_max
    side: Side | None
    if cfg.use_btc_distance:
        delta = snap.btc_minus_ptb()
        if delta is None:
            snap.remember_mids()
            return Decision(None, "no_btc", None, limit)
        if not _distance_ok(abs(delta), cfg):
            snap.remember_mids()
            return Decision(None, "btc_out", None, limit)
        side = "up" if delta > 0 else "down"
        _note_mid_enter(snap, side, lo, hi)
        if not snap.mid_entered(side):
            snap.remember_mids()
            return Decision(None, "odds_out", snap.ask_for(side), limit)
    else:
        side = _pick_entered_side(snap, lo, hi)
        if side is None:
            snap.remember_mids()
            ask = snap.up_ask if snap.up_ask is not None else snap.down_ask
            return Decision(None, "odds_out", ask, limit)

    ask = snap.ask_for(side)
    if ask is None:
        snap.remember_mids()
        return Decision(None, "no_ask", None, limit)
    if not _ask_fillable(ask, cfg):
        snap.remember_mids()
        return Decision(None, "ask_above_cap", ask, limit)

    if cfg.use_twap:
        twap_delta = snap.twap_minus_ptb()
        if twap_delta is None:
            snap.remember_mids()
            return Decision(None, "no_twap", ask, limit)
        if side == "up" and not (twap_delta > 0):
            snap.remember_mids()
            return Decision(None, "twap_disagree", ask, limit)
        if side == "down" and not (twap_delta < 0):
            snap.remember_mids()
            return Decision(None, "twap_disagree", ask, limit)

    if cfg.use_venues:
        venues = snap.venues_on_side(side)
        if venues < cfg.min_venues:
            snap.remember_mids()
            return Decision(None, "venues", ask, limit)

    snap.remember_mids()
    return Decision(side, "ok", ask, limit)


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


def _note_mid_enter(snap: LiveSnapshot, side: Side, lo: float, hi: float) -> None:
    curr = snap.mid_for(side)
    prev = snap.prev_up_mid if side == "up" else snap.prev_down_mid
    if _entered_band(prev, curr, lo, hi):
        snap.mark_mid_entered(side)


def _pick_entered_side(snap: LiveSnapshot, lo: float, hi: float) -> Side | None:
    _note_mid_enter(snap, "up", lo, hi)
    _note_mid_enter(snap, "down", lo, hi)
    up_hit = snap.mid_entered("up")
    down_hit = snap.mid_entered("down")
    if up_hit and down_hit:
        mid = (lo + hi) / 2
        up = snap.mid_for("up")
        down = snap.mid_for("down")
        up_dist = abs((up if up is not None else mid) - mid)
        down_dist = abs((down if down is not None else mid) - mid)
        return "up" if up_dist <= down_dist else "down"
    if up_hit:
        return "up"
    if down_hit:
        return "down"
    return None


def _ask_fillable(ask: float, cfg: TraderConfig) -> bool:
    """Ask must be fillable at or below the FAK cap (may exceed trigger band)."""
    lo, hi = cfg.odds_min, cfg.odds_max
    cap = cfg.effective_fak_limit()
    if lo < hi:
        return lo <= ask <= cap
    if lo < 0.5:
        return ask <= cap
    return lo <= ask <= cap
