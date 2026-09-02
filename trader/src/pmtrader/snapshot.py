"""In-memory book, spots, TWAP, and price-to-beat for the current window."""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field

VENUE_SOURCES = ("binance_spot", "coinbase_spot", "bybit_spot", "binance_futures")
SPOT_SOURCES = ("binance_spot", "coinbase_spot", "bybit_spot")


def _f(value: object | None) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


@dataclass
class LiveSnapshot:
    slug: str = "-"
    window_start: int = 0
    window_end: int = 0
    up_token_id: str = ""
    down_token_id: str = ""
    ptb: float | None = None
    ptb_source: str | None = None
    twap: float | None = None
    up_ask: float | None = None
    down_ask: float | None = None
    up_mid: float | None = None
    down_mid: float | None = None
    spots: dict[str, float | None] = field(
        default_factory=lambda: {name: None for name in VENUE_SOURCES}
    )
    # Venue whose price stands in for BTC (or "median" of the three spots).
    btc_source: str = "binance_spot"
    # Mids of the previous once-per-second sample, for the Lab's "enter the odds
    # band" test (prev sample outside the band, this sample inside). Null until
    # the first in-watch sample with a book; nothing is remembered outside the watch.
    prev_up_mid: float | None = None
    prev_down_mid: float | None = None
    in_watch: bool = False

    def reset_window(
        self,
        *,
        slug: str,
        window_start: int,
        window_end: int,
        up_token_id: str,
        down_token_id: str,
        ptb: float | None,
        ptb_source: str | None,
    ) -> None:
        self.slug = slug
        self.window_start = window_start
        self.window_end = window_end
        self.up_token_id = up_token_id
        self.down_token_id = down_token_id
        self.ptb = ptb
        self.ptb_source = ptb_source
        self.up_ask = None
        self.down_ask = None
        self.up_mid = None
        self.down_mid = None
        self.clear_odds_memory()

    def set_ptb(self, value: str | float | None, source: str) -> None:
        parsed = _f(value)
        if parsed is None:
            return
        self.ptb = parsed
        self.ptb_source = source

    def apply_odds(self, tick: dict) -> None:
        self.up_ask = _f(tick.get("up_ask"))
        self.down_ask = _f(tick.get("down_ask"))
        self.up_mid = _f(tick.get("up_mid"))
        self.down_mid = _f(tick.get("down_mid"))

    def apply_price(self, tick: dict) -> None:
        source = str(tick.get("source") or "")
        if source not in self.spots:
            return
        self.spots[source] = _f(tick.get("price"))

    def apply_twap(self, tick: dict) -> None:
        self.twap = _f(tick.get("value"))

    @property
    def btc(self) -> float | None:
        if self.btc_source != "median":
            return self.spots.get(self.btc_source)
        values = [p for name in SPOT_SOURCES if (p := self.spots.get(name)) is not None]
        if not values:
            return None
        return statistics.median(values)

    def spot_deltas(self) -> dict[str, float]:
        if self.ptb is None:
            return {}
        out: dict[str, float] = {}
        for name in VENUE_SOURCES:
            price = self.spots.get(name)
            if price is not None:
                out[name] = round(price - self.ptb, 2)
        return out

    def btc_minus_ptb(self) -> float | None:
        if self.btc is None or self.ptb is None:
            return None
        return self.btc - self.ptb

    def twap_minus_ptb(self) -> float | None:
        if self.twap is None or self.ptb is None:
            return None
        return self.twap - self.ptb

    def venues_on_side(self, side: str) -> int:
        if self.ptb is None:
            return 0
        count = 0
        for price in self.spots.values():
            if price is None:
                continue
            if side == "up" and price > self.ptb:
                count += 1
            elif side == "down" and price < self.ptb:
                count += 1
        return count

    def ask_for(self, side: str) -> float | None:
        return self.up_ask if side == "up" else self.down_ask

    def mid_for(self, side: str) -> float | None:
        mid = self.up_mid if side == "up" else self.down_mid
        if mid is not None:
            return mid
        return self.ask_for(side)

    def clear_odds_memory(self) -> None:
        self.prev_up_mid = None
        self.prev_down_mid = None
        self.in_watch = False

    def remember_mids(self) -> None:
        up = self.mid_for("up")
        down = self.mid_for("down")
        if up is not None:
            self.prev_up_mid = up
        if down is not None:
            self.prev_down_mid = down

    def token_for(self, side: str) -> str:
        return self.up_token_id if side == "up" else self.down_token_id
