"""Hold-to-expiry strategies for 5m UP/DOWN."""

from __future__ import annotations

from dataclasses import dataclass
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


def _odds_hit(value: float | None, hit_odds: float) -> bool:
    if value is None:
        return False
    return value >= hit_odds if hit_odds >= 0.5 else value <= hit_odds


def _spot_side(snap: Snapshot) -> Signal | None:
    if snap.btc is None:
        return None
    if snap.btc > snap.ptb:
        return "up"
    if snap.btc < snap.ptb:
        return "down"
    return None


def _pick_odds_side(snap: Snapshot, hit_odds: float) -> Signal | None:
    up = _side_odds(snap.up_mid, snap.up_ask)
    down = _side_odds(snap.down_mid, snap.down_ask)
    up_hit = _odds_hit(up, hit_odds)
    down_hit = _odds_hit(down, hit_odds)
    buy_favorite = hit_odds >= 0.5
    if up_hit and down_hit:
        if buy_favorite:
            return "up" if up >= down else "down"
        return "up" if up <= down else "down"
    if up_hit:
        return "up"
    if down_hit:
        return "down"
    return None


@dataclass(slots=True)
class SpotLead:
    """Buy the side matching spot vs PTB once the move is large enough and still cheap."""

    entry_after_s: float = 15.0
    min_distance: float = 10.0
    max_ask: float = 0.75
    name: str = "spot_lead"

    def on_tick(self, snap: Snapshot) -> Signal | None:
        if snap.elapsed_s < self.entry_after_s:
            return None
        if snap.btc is None:
            return None
        dist = snap.btc - snap.ptb
        if abs(dist) < self.min_distance:
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

    Needs odds and/or spot to pick a side. Hold to expiry is the engine's job.
    """

    last_minutes: float = 3.0
    use_last_minutes: bool = True
    use_odds: bool = True
    hit_odds: float = 0.25
    use_spot: bool = False
    min_distance: float = 10.0
    use_twap: bool = False
    use_volume: bool = False
    min_volume: float = 0.0
    use_venues: bool = False
    min_venues: int = 2
    name: str = "combo"

    def on_tick(self, snap: Snapshot) -> Signal | None:
        if self.use_last_minutes and snap.seconds_left > self.last_minutes * 60:
            return None
        if not self.use_odds and not self.use_spot:
            return None

        side: Signal | None
        if self.use_spot:
            side = _spot_side(snap)
            if side is None:
                return None
            if snap.btc is None or abs(snap.btc - snap.ptb) < self.min_distance:
                return None
            if self.use_odds:
                odds = _side_odds(
                    snap.up_mid if side == "up" else snap.down_mid,
                    snap.up_ask if side == "up" else snap.down_ask,
                )
                if not _odds_hit(odds, self.hit_odds):
                    return None
        else:
            side = _pick_odds_side(snap, self.hit_odds)
            if side is None:
                return None

        if self.use_twap:
            if snap.twap is None:
                return None
            if side == "up" and not (snap.twap > snap.ptb):
                return None
            if side == "down" and not (snap.twap < snap.ptb):
                return None

        if self.use_volume:
            if snap.volume_base is None or snap.volume_base < self.min_volume:
                return None

        if self.use_venues:
            if snap.venues_on_side(side) < self.min_venues:
                return None

        return side


def _odds_only_combo(hit_odds: float, last_minutes: float) -> ComboRules:
    cents = int(round(hit_odds * 100))
    return ComboRules(
        last_minutes=last_minutes,
        use_last_minutes=True,
        use_odds=True,
        hit_odds=hit_odds,
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
    max_ask: float = 0.75,
    cheap_ask: float = 0.55,
    hit_odds: float = 0.75,
    last_minutes: float = 3.0,
    use_last_minutes: bool = True,
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
            use_odds=use_odds,
            hit_odds=hit_odds,
            use_spot=use_spot,
            min_distance=min_distance,
            use_twap=use_twap,
            use_volume=use_volume,
            min_volume=min_volume,
            use_venues=use_venues,
            min_venues=min_venues,
        )
    raise ValueError(f"Unknown strategy {name!r}. Use combo, hit_odds, spot_lead, or odds_lag.")
