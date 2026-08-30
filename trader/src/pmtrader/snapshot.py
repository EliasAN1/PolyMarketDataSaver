"""In-memory book, spots, TWAP, and price-to-beat for the current window."""

from __future__ import annotations

from dataclasses import dataclass, field

VENUE_SOURCES = ("binance_spot", "coinbase_spot", "bybit_spot", "binance_futures")


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
    # Ask was seen outside the odds band *after* the last-N entry window opened.
    seen_outside_up: bool = False
    seen_outside_down: bool = False

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
        self.seen_outside_up = False
        self.seen_outside_down = False

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
        return self.spots["binance_spot"] or self.spots["coinbase_spot"] or self.spots["bybit_spot"]

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

    def seen_outside(self, side: str) -> bool:
        return self.seen_outside_up if side == "up" else self.seen_outside_down

    def mark_outside(self, side: str) -> None:
        if side == "up":
            self.seen_outside_up = True
        else:
            self.seen_outside_down = True

    def token_for(self, side: str) -> str:
        return self.up_token_id if side == "up" else self.down_token_id
