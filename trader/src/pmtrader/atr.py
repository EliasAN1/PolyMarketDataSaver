"""Binance spot BTCUSDT 5m Wilder ATR(14) / 288-candle mean.

The ratio is taken from the candle that closed at the window open
(open time = window_start - 300s). The still-open candle is never included.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

BINANCE_KLINES = "https://api.binance.com/api/v3/klines"
CANDLE_S = 300
ATR_PERIOD = 14
ATR_MEAN_BARS = 288
MIN_CANDLES = 400
FETCH_LIMIT = 500


@dataclass(frozen=True, slots=True)
class Candle:
    open_time_s: int
    high: float
    low: float
    close: float


@dataclass(frozen=True, slots=True)
class AtrReading:
    atr: float
    atr_mean: float
    ratio: float
    asof_open_s: int


@dataclass(frozen=True, slots=True)
class AtrBlock:
    reason: str


def wilder_atr(high: list[float], low: list[float], close: list[float]) -> list[float | None]:
    """Wilder ATR(14). The first value is the mean of the first 14 true ranges."""
    n = len(close)
    atr: list[float | None] = [None] * n
    if n < ATR_PERIOD:
        return atr
    tr = [high[0] - low[0]]
    for i in range(1, n):
        tr.append(
            max(
                high[i] - low[i],
                abs(high[i] - close[i - 1]),
                abs(low[i] - close[i - 1]),
            )
        )
    atr[ATR_PERIOD - 1] = sum(tr[:ATR_PERIOD]) / ATR_PERIOD
    for i in range(ATR_PERIOD, n):
        previous = atr[i - 1]
        assert previous is not None
        atr[i] = (previous * (ATR_PERIOD - 1) + tr[i]) / ATR_PERIOD
    return atr


def assess(candles: list[Candle], window_start: int, now_s: float) -> AtrReading | AtrBlock:
    """Ratio for one window, or a skip reason. Does not invent a stand-in bar."""
    asof = int(window_start) - CANDLE_S
    closed_by_open: dict[int, Candle] = {}
    for candle in candles:
        if candle.open_time_s >= window_start:
            continue
        if candle.open_time_s + CANDLE_S > now_s:
            continue
        closed_by_open[candle.open_time_s] = candle
    closed = [closed_by_open[key] for key in sorted(closed_by_open)]
    if not closed or closed[-1].open_time_s < asof:
        return AtrBlock("atr_stale" if closed else "atr_missing")
    if closed[-1].open_time_s != asof:
        return AtrBlock("atr_missing")
    for prev, nxt in zip(closed, closed[1:]):
        if nxt.open_time_s - prev.open_time_s != CANDLE_S:
            return AtrBlock("atr_gap")
    if len(closed) < MIN_CANDLES:
        return AtrBlock("atr_warmup")
    # ATR at the asof bar needs 287 earlier ATR values, which themselves need
    # the 14-bar seed. 400 bars is enough; use the whole closed prefix.
    high = [candle.high for candle in closed]
    low = [candle.low for candle in closed]
    close = [candle.close for candle in closed]
    atr = wilder_atr(high, low, close)
    end = len(atr) - 1
    window = atr[end - ATR_MEAN_BARS + 1 : end + 1]
    if len(window) < ATR_MEAN_BARS or any(value is None for value in window):
        return AtrBlock("atr_warmup")
    values = [float(value) for value in window if value is not None]
    mean = sum(values) / len(values)
    latest = values[-1]
    if mean <= 0:
        return AtrBlock("atr_missing")
    return AtrReading(atr=latest, atr_mean=mean, ratio=latest / mean, asof_open_s=asof)


async def fetch_btcusdt_5m(limit: int = FETCH_LIMIT) -> list[Candle]:
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(
            BINANCE_KLINES,
            params={"symbol": "BTCUSDT", "interval": "5m", "limit": limit},
        )
        response.raise_for_status()
        payload = response.json()
    candles: list[Candle] = []
    if not isinstance(payload, list):
        return candles
    for row in payload:
        candles.append(
            Candle(
                open_time_s=int(row[0]) // 1000,
                high=float(row[2]),
                low=float(row[3]),
                close=float(row[4]),
            )
        )
    candles.sort(key=lambda candle: candle.open_time_s)
    return candles


class AtrBook:
    """Fetches 5m candles and caches one ratio per window start."""

    def __init__(self, fetcher=fetch_btcusdt_5m) -> None:
        self._fetcher = fetcher
        self.candles: list[Candle] = []
        self._cache: dict[int, AtrReading | AtrBlock] = {}

    async def load_window(self, window_start: int, now_s: float) -> AtrReading | AtrBlock:
        cached = self._cache.get(window_start)
        if cached is not None:
            return cached
        result: AtrReading | AtrBlock = AtrBlock("atr_missing")
        for attempt in range(3):
            try:
                self.candles = await self._fetcher()
            except Exception:
                logger.exception("Binance 5m candle fetch failed")
                result = AtrBlock("atr_missing")
            else:
                if len(self.candles) > FETCH_LIMIT:
                    self.candles = self.candles[-FETCH_LIMIT:]
                result = assess(self.candles, window_start, now_s)
                if not isinstance(result, AtrBlock) or result.reason not in {"atr_missing", "atr_stale"}:
                    break
            if attempt < 2:
                await asyncio.sleep(0.4)
        self._cache[window_start] = result
        return result
