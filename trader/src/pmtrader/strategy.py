"""Combo entry filters: last-N minutes, odds band, BTC distance, TWAP, venues."""

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
    limit = cfg.odds_max
    if snap.window_end <= 0:
        return Decision(None, "no_window", None, limit)

    seconds_left = snap.window_end - now_s
    if seconds_left < cfg.min_seconds_left:
        return Decision(None, "too_late", None, limit)
    if cfg.use_entry_last and seconds_left > cfg.entry_last_minutes * 60:
        return Decision(None, "too_early", None, limit)

    if snap.ptb is None:
        return Decision(None, "no_ptb", None, limit)

    side: Side | None
    if cfg.use_btc_distance:
        delta = snap.btc_minus_ptb()
        if delta is None:
            return Decision(None, "no_btc", None, limit)
        if abs(delta) < cfg.min_btc_away:
            return Decision(None, "btc_too_close", None, limit)
        side = "up" if delta > 0 else "down"
        ask = snap.ask_for(side)
        if ask is None:
            return Decision(None, "no_ask", None, limit)
        if not _in_band(ask, cfg):
            snap.mark_outside(side)
            return Decision(None, "odds_out", ask, limit)
        if not snap.seen_outside(side):
            return Decision(None, "already_in", ask, limit)
    else:
        side = _pick_odds_side(snap, cfg)
        if side is None:
            ask = snap.up_ask if snap.up_ask is not None else snap.down_ask
            reason = "already_in" if _any_already_in(snap, cfg) else "odds_out"
            return Decision(None, reason, ask, limit)
        ask = snap.ask_for(side)

    if cfg.use_twap:
        twap_delta = snap.twap_minus_ptb()
        if twap_delta is None:
            return Decision(None, "no_twap", ask, limit)
        if side == "up" and not (twap_delta > 0):
            return Decision(None, "twap_disagree", ask, limit)
        if side == "down" and not (twap_delta < 0):
            return Decision(None, "twap_disagree", ask, limit)

    if cfg.use_venues:
        venues = snap.venues_on_side(side)
        if venues < cfg.min_venues:
            return Decision(None, "venues", ask, limit)

    return Decision(side, "ok", ask, limit)


def _in_band(ask: float | None, cfg: TraderConfig) -> bool:
    return ask is not None and cfg.odds_min <= ask <= cfg.odds_max


def _pick_odds_side(snap: LiveSnapshot, cfg: TraderConfig) -> Side | None:
    if not _in_band(snap.up_ask, cfg) and snap.up_ask is not None:
        snap.mark_outside("up")
    if not _in_band(snap.down_ask, cfg) and snap.down_ask is not None:
        snap.mark_outside("down")
    up_hit = _in_band(snap.up_ask, cfg) and snap.seen_outside("up")
    down_hit = _in_band(snap.down_ask, cfg) and snap.seen_outside("down")
    buy_favorite = cfg.odds_max >= 0.5
    if up_hit and down_hit:
        assert snap.up_ask is not None and snap.down_ask is not None
        if buy_favorite:
            return "up" if snap.up_ask >= snap.down_ask else "down"
        return "up" if snap.up_ask <= snap.down_ask else "down"
    if up_hit:
        return "up"
    if down_hit:
        return "down"
    return None


def _any_already_in(snap: LiveSnapshot, cfg: TraderConfig) -> bool:
    up = _in_band(snap.up_ask, cfg) and not snap.seen_outside("up")
    down = _in_band(snap.down_ask, cfg) and not snap.seen_outside("down")
    return up or down
