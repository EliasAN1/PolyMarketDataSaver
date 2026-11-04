"""In-memory book, spots, TWAP, and price-to-beat for the current window."""

from __future__ import annotations

import statistics
import time
from dataclasses import dataclass, field

VENUE_SOURCES = ("binance_spot", "coinbase_spot", "bybit_spot", "binance_futures")
SPOT_SOURCES = ("binance_spot", "coinbase_spot", "bybit_spot")
# A silent socket keeps its last print forever. Strategy Lab's tape only
# forward-fills ticks the collector actually received, so a dead Binance
# feed on the trader must not be mixed with a live CLOB book.
PRICE_STALE_AFTER_S = 5.0


def _f(value: object | None) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _now_s(now_s: float | None) -> float:
    return time.time() if now_s is None else now_s


def _tick_s(tick: dict) -> float:
    recv = tick.get("recv_ts_ms")
    if recv is not None:
        try:
            return float(recv) / 1000.0
        except (TypeError, ValueError):
            pass
    return time.time()


def _fresh(price: float | None, ts: float | None, now_s: float) -> float | None:
    if price is None or ts is None:
        return None
    if now_s - ts > PRICE_STALE_AFTER_S:
        return None
    return price


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
    spot_ts: dict[str, float | None] = field(
        default_factory=lambda: {name: None for name in VENUE_SOURCES}
    )
    twap_ts: float | None = None
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
        self.twap = None
        self.twap_ts = None
        for name in VENUE_SOURCES:
            self.spots[name] = None
            self.spot_ts[name] = None
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
        price = _f(tick.get("price"))
        if price is None:
            return
        self.spots[source] = price
        self.spot_ts[source] = _tick_s(tick)

    def apply_twap(self, tick: dict) -> None:
        value = _f(tick.get("value"))
        if value is None:
            return
        self.twap = value
        self.twap_ts = _tick_s(tick)

    @property
    def btc(self) -> float | None:
        return self.btc_at()

    def btc_at(self, now_s: float | None = None) -> float | None:
        now = _now_s(now_s)
        if self.btc_source != "median":
            return self._fresh_spot(self.btc_source, now)
        values = [p for name in SPOT_SOURCES if (p := self._fresh_spot(name, now)) is not None]
        if not values:
            return None
        return statistics.median(values)

    def twap_at(self, now_s: float | None = None) -> float | None:
        return _fresh(self.twap, self.twap_ts, _now_s(now_s))

    def spot_deltas(self, now_s: float | None = None) -> dict[str, float]:
        if self.ptb is None:
            return {}
        now = _now_s(now_s)
        out: dict[str, float] = {}
        for name in VENUE_SOURCES:
            price = self._fresh_spot(name, now)
            if price is not None:
                out[name] = round(price - self.ptb, 2)
        return out

    def btc_minus_ptb(self, now_s: float | None = None) -> float | None:
        btc = self.btc_at(now_s)
        if btc is None or self.ptb is None:
            return None
        return btc - self.ptb

    def twap_minus_ptb(self, now_s: float | None = None) -> float | None:
        twap = self.twap_at(now_s)
        if twap is None or self.ptb is None:
            return None
        return twap - self.ptb

    def venues_on_side(self, side: str, now_s: float | None = None) -> int:
        if self.ptb is None:
            return 0
        now = _now_s(now_s)
        count = 0
        for name in VENUE_SOURCES:
            price = self._fresh_spot(name, now)
            if price is None:
                continue
            if side == "up" and price > self.ptb:
                count += 1
            elif side == "down" and price < self.ptb:
                count += 1
        return count

    def _fresh_spot(self, name: str, now_s: float) -> float | None:
        return _fresh(self.spots.get(name), self.spot_ts.get(name), now_s)

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
