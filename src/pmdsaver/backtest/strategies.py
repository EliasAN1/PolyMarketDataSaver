"""Hold-to-expiry strategies for 5m UP/DOWN."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from pmdsaver.backtest.replay import Snapshot

Signal = str  # "up" | "down"


class Strategy(Protocol):
    name: str

    def on_tick(self, snap: Snapshot) -> Signal | None: ...


def _side_odds(mid: float | None, ask: float | None) -> float | None:
    if mid is not None:
        return mid
    return ask


def _clamp_band(lo: float, hi: float) -> tuple[float, float]:
    lo = min(max(lo, 0.01), 0.99)
    hi = min(max(hi, 0.01), 0.99)
    if lo > hi:
        lo, hi = hi, lo
    return lo, hi


def _in_band(value: float | None, lo: float, hi: float) -> bool:
    if value is None:
        return False
    return lo <= value <= hi


def _entered_band(prev: float | None, curr: float | None, lo: float, hi: float) -> bool:
    """True when odds move from outside the band to inside it.

    The first sample of a window only sets a baseline — already sitting in
    (or outside) the band does not fire. A point target (lo == hi) fires when
    the level is crossed from either side.
    """
    if curr is None or prev is None:
        return False
    if lo < hi:
        return (not _in_band(prev, lo, hi)) and _in_band(curr, lo, hi)
    return (prev < lo <= curr) or (prev > lo >= curr)


def _distance_in_band(abs_dist: float, min_distance: float, max_distance: float | None) -> bool:
    if abs_dist < min_distance:
        return False
    if max_distance is not None and abs_dist > max_distance:
        return False
    return True


def _spot_side(snap: Snapshot) -> Signal | None:
    if snap.btc is None:
        return None
    if snap.btc > snap.ptb:
        return "up"
    if snap.btc < snap.ptb:
        return "down"
    return None


def _pick_entered_side(
    prev_up: float | None,
    up: float | None,
    prev_down: float | None,
    down: float | None,
    lo: float,
    hi: float,
) -> Signal | None:
    up_in = _entered_band(prev_up, up, lo, hi)
    down_in = _entered_band(prev_down, down, lo, hi)
    if up_in and down_in:
        mid = (lo + hi) / 2
        up_dist = abs((up or mid) - mid)
        down_dist = abs((down or mid) - mid)
        return "up" if up_dist <= down_dist else "down"
    if up_in:
        return "up"
    if down_in:
        return "down"
    return None


@dataclass(slots=True)
class SpotLead:
    """Buy the side matching spot vs PTB once the move is large enough and still cheap."""

    entry_after_s: float = 15.0
    min_distance: float = 10.0
    max_distance: float | None = None
    max_ask: float = 0.75
    name: str = "spot_lead"

    def on_tick(self, snap: Snapshot) -> Signal | None:
        if snap.elapsed_s < self.entry_after_s:
            return None
        if snap.btc is None:
            return None
        dist = snap.btc - snap.ptb
        if not _distance_in_band(abs(dist), self.min_distance, self.max_distance):
            return None
        if dist > 0 and snap.up_ask is not None and snap.up_ask <= self.max_ask:
            return "up"
        if dist < 0 and snap.down_ask is not None and snap.down_ask <= self.max_ask:
            return "down"
        return None


@dataclass(slots=True)
class OddsLag:
    """Buy the spot-implied side while the CLOB ask is still cheap."""

    cheap_ask: float = 0.55
    name: str = "odds_lag"

    def on_tick(self, snap: Snapshot) -> Signal | None:
        if snap.btc is None:
            return None
        if snap.btc > snap.ptb and snap.up_ask is not None and snap.up_ask <= self.cheap_ask:
            return "up"
        if snap.btc < snap.ptb and snap.down_ask is not None and snap.down_ask <= self.cheap_ask:
            return "down"
        return None


@dataclass(slots=True)
class ComboRules:
    """AND optional entry filters. Unchecked filters are ignored.

    Odds fire only when a side *enters* [odds_lo, odds_hi] this tick. Already
    inside (or outside) at the first sample of the watch window does not count.
    Hold to expiry is the engine's job.
    """

    last_minutes: float = 3.0
    use_last_minutes: bool = True
    elapsed_from_min: float | None = None
    elapsed_to_min: float | None = None
    use_odds: bool = True
    hit_odds: float = 0.25
    odds_lo: float | None = None
    odds_hi: float | None = None
    use_spot: bool = False
    min_distance: float = 10.0
    max_distance: float | None = None
    use_twap: bool = False
    use_volume: bool = False
    min_volume: float = 0.0
    use_venues: bool = False
    min_venues: int = 2
    name: str = "combo"
    _window_id: int | None = field(default=None, init=False, repr=False, compare=False)
    _prev_up: float | None = field(default=None, init=False, repr=False, compare=False)
    _prev_down: float | None = field(default=None, init=False, repr=False, compare=False)

    def _band(self) -> tuple[float, float]:
        lo = self.odds_lo if self.odds_lo is not None else self.hit_odds
        hi = self.odds_hi if self.odds_hi is not None else self.hit_odds
        return _clamp_band(lo, hi)

    def _reset_if_new_window(self, snap: Snapshot) -> None:
        if snap.window_id == self._window_id:
            return
        self._window_id = snap.window_id
        self._prev_up = None
        self._prev_down = None

    def _remember_odds(self, snap: Snapshot) -> None:
        self._prev_up = _side_odds(snap.up_mid, snap.up_ask)
        self._prev_down = _side_odds(snap.down_mid, snap.down_ask)

    def _in_watch(self, snap: Snapshot) -> bool:
        if self.elapsed_from_min is not None or self.elapsed_to_min is not None:
            lo = 0.0 if self.elapsed_from_min is None else float(self.elapsed_from_min) * 60.0
            hi = 1e12 if self.elapsed_to_min is None else float(self.elapsed_to_min) * 60.0
            if lo > hi:
                lo, hi = hi, lo
            return lo <= snap.elapsed_s <= hi
        if self.use_last_minutes:
            return snap.seconds_left <= self.last_minutes * 60
        return True

    def on_tick(self, snap: Snapshot) -> Signal | None:
        self._reset_if_new_window(snap)
        if not self._in_watch(snap):
            return None
        if not self.use_odds and not self.use_spot:
            return None

        lo, hi = self._band()
        side: Signal | None
        if self.use_spot:
            side = _spot_side(snap)
            if side is None:
                self._remember_odds(snap)
                return None
            if snap.btc is None or not _distance_in_band(
                abs(snap.btc - snap.ptb), self.min_distance, self.max_distance
            ):
                self._remember_odds(snap)
                return None
            if self.use_odds:
                curr = _side_odds(
                    snap.up_mid if side == "up" else snap.down_mid,
                    snap.up_ask if side == "up" else snap.down_ask,
                )
                prev = self._prev_up if side == "up" else self._prev_down
                if not _entered_band(prev, curr, lo, hi):
                    self._remember_odds(snap)
                    return None
        else:
            side = _pick_entered_side(
                self._prev_up,
                _side_odds(snap.up_mid, snap.up_ask),
                self._prev_down,
                _side_odds(snap.down_mid, snap.down_ask),
                lo,
                hi,
            )
            if side is None:
                self._remember_odds(snap)
                return None

        if self.use_twap:
            if snap.twap is None:
                self._remember_odds(snap)
                return None
            if side == "up" and not (snap.twap > snap.ptb):
                self._remember_odds(snap)
                return None
            if side == "down" and not (snap.twap < snap.ptb):
                self._remember_odds(snap)
                return None

        if self.use_volume:
            if snap.volume_base is None or snap.volume_base < self.min_volume:
                self._remember_odds(snap)
                return None

        if self.use_venues:
            if snap.venues_on_side(side) < self.min_venues:
                self._remember_odds(snap)
                return None

        self._remember_odds(snap)
        return side


def _odds_only_combo(hit_odds: float, last_minutes: float) -> ComboRules:
    cents = int(round(hit_odds * 100))
    return ComboRules(
        last_minutes=last_minutes,
        use_last_minutes=True,
        use_odds=True,
        hit_odds=hit_odds,
        odds_lo=hit_odds,
        odds_hi=hit_odds,
        use_spot=False,
        use_twap=False,
        use_volume=False,
        use_venues=False,
        name=f"hit_{cents}",
    )


def build_strategy(
    name: str,
    *,
    entry_after_s: float = 15.0,
    min_distance: float = 10.0,
    max_distance: float | None = None,
    max_ask: float = 0.75,
    cheap_ask: float = 0.55,
    hit_odds: float = 0.75,
    odds_lo: float | None = None,
    odds_hi: float | None = None,
    last_minutes: float = 3.0,
    use_last_minutes: bool = True,
    elapsed_from_min: float | None = None,
    elapsed_to_min: float | None = None,
    use_odds: bool = True,
    use_spot: bool = False,
    use_twap: bool = False,
    use_volume: bool = False,
    min_volume: float = 0.0,
    use_venues: bool = False,
    min_venues: int = 2,
) -> Strategy:
    key = name.strip().lower()
    if key == "spot_lead":
        return SpotLead(
            entry_after_s=entry_after_s,
            min_distance=min_distance,
            max_distance=max_distance,
            max_ask=max_ask,
        )
    if key == "odds_lag":
        return OddsLag(cheap_ask=cheap_ask)
    if key in ("hit_75", "hit_25", "hit_odds"):
        return _odds_only_combo(hit_odds, last_minutes)
    if key == "combo":
        return ComboRules(
            last_minutes=last_minutes,
            use_last_minutes=use_last_minutes,
            elapsed_from_min=elapsed_from_min,
            elapsed_to_min=elapsed_to_min,
            use_odds=use_odds,
            hit_odds=hit_odds,
            odds_lo=odds_lo,
            odds_hi=odds_hi,
            use_spot=use_spot,
            min_distance=min_distance,
            max_distance=max_distance,
            use_twap=use_twap,
            use_volume=use_volume,
            min_volume=min_volume,
            use_venues=use_venues,
            min_venues=min_venues,
        )
    raise ValueError(f"Unknown strategy {name!r}. Use combo, hit_odds, spot_lead, or odds_lag.")
