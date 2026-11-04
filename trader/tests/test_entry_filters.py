"""Delay and ATR entry filters. The ATR check is compared to a separate formula."""

from __future__ import annotations

import unittest
from pathlib import Path

from pmtrader.atr import AtrBlock, AtrReading, Candle, assess
from pmtrader.config import TraderConfig, load_config
from pmtrader.snapshot import LiveSnapshot
from pmtrader.strategy import evaluate


def _series(n: int, start: int = 1_700_000_000) -> list[Candle]:
    start -= start % 300
    candles: list[Candle] = []
    for i in range(n):
        base = 100.0 + (i % 17) * 0.3
        candles.append(
            Candle(
                open_time_s=start + i * 300,
                high=base + 1.5 + (i % 5) * 0.1,
                low=base - 0.8,
                close=base + ((i % 3) - 1) * 0.2,
            )
        )
    return candles


def _independent_ratio(candles: list[Candle]) -> tuple[float, float, float]:
    """Wilder ATR(14) / 288-mean written out separately from pmtrader.atr."""
    high = [candle.high for candle in candles]
    low = [candle.low for candle in candles]
    close = [candle.close for candle in candles]
    true_range = [high[0] - low[0]]
    for i in range(1, len(candles)):
        true_range.append(
            max(
                high[i] - low[i],
                abs(high[i] - close[i - 1]),
                abs(low[i] - close[i - 1]),
            )
        )
    atr = sum(true_range[:14]) / 14
    values = [None] * 13 + [atr]
    for i in range(14, len(true_range)):
        atr = (13 * atr + true_range[i]) / 14
        values.append(atr)
    tail = values[-288:]
    mean = sum(tail) / 288
    return tail[-1], mean, tail[-1] / mean


def _apply(snap: LiveSnapshot, result: AtrReading | AtrBlock) -> None:
    if isinstance(result, AtrReading):
        snap.atr_value = result.atr
        snap.atr_mean = result.atr_mean
        snap.atr_ratio = result.ratio
        snap.atr_block = None
    else:
        snap.atr_value = None
        snap.atr_mean = None
        snap.atr_ratio = None
        snap.atr_block = result.reason


def _ready_snap(window_start: int = 1_000_000) -> LiveSnapshot:
    snap = LiveSnapshot(
        slug=f"btc-updown-5m-{window_start}",
        window_start=window_start,
        window_end=window_start + 300,
        ptb=100.0,
        up_mid=0.50,
        down_mid=0.50,
        up_ask=0.51,
        down_ask=0.51,
        prev_up_mid=0.40,
        prev_down_mid=0.40,
        in_watch=True,
        atr_value=10.0,
        atr_mean=10.0,
        atr_ratio=1.0,
    )
    return snap


def _cfg() -> TraderConfig:
    return TraderConfig(
        elapsed_from_min=0.0,
        elapsed_to_min=2.0,
        odds_min=0.45,
        odds_max=0.55,
        fak_limit=0.60,
        use_btc_distance=False,
        use_twap=False,
        use_venues=False,
        min_delay_seconds=10,
        atr_mean_max=1.125,
    )


class AtrMathTest(unittest.TestCase):
    def test_ratio_matches_independent_calculation(self):
        candles = _series(420)
        window_start = candles[-1].open_time_s + 300
        result = assess(candles, window_start, now_s=window_start + 5)
        self.assertIsInstance(result, AtrReading)
        assert isinstance(result, AtrReading)
        atr, mean, ratio = _independent_ratio(candles)
        self.assertAlmostEqual(result.atr, atr, places=9)
        self.assertAlmostEqual(result.atr_mean, mean, places=9)
        self.assertAlmostEqual(result.ratio, ratio, places=9)
        self.assertEqual(result.asof_open_s, window_start - 300)

    def test_open_candle_is_never_used(self):
        candles = _series(420)
        window_start = candles[-1].open_time_s + 300
        clean = assess(candles, window_start, now_s=window_start + 5)
        polluted = candles + [Candle(window_start, high=1_000_000.0, low=0.0, close=1_000_000.0)]
        dirty = assess(polluted, window_start, now_s=window_start + 5)
        self.assertIsInstance(clean, AtrReading)
        self.assertIsInstance(dirty, AtrReading)
        assert isinstance(clean, AtrReading) and isinstance(dirty, AtrReading)
        self.assertEqual(dirty.atr, clean.atr)
        self.assertEqual(dirty.atr_mean, clean.atr_mean)
        self.assertEqual(dirty.ratio, clean.ratio)
        only_open = assess(
            [Candle(window_start, high=1_000_000.0, low=0.0, close=1_000_000.0)],
            window_start,
            now_s=window_start + 10,
        )
        self.assertEqual(getattr(only_open, "reason", None), "atr_missing")

    def test_missing_or_stale_or_gapped_series_blocks(self):
        candles = _series(420)
        window_start = candles[-1].open_time_s + 300
        now = window_start + 5
        self.assertEqual(assess([], window_start, now).reason, "atr_missing")
        stale = assess(candles[:-1], window_start, now)
        self.assertEqual(stale.reason, "atr_stale")
        gapped = candles[:100] + candles[101:]
        self.assertEqual(assess(gapped, window_start, now).reason, "atr_gap")

        snap = _ready_snap()
        _apply(snap, stale)
        decision = evaluate(snap, _cfg(), now_s=snap.window_start + 11)
        self.assertFalse(decision.ok)
        self.assertEqual(decision.reason, "atr_stale")

        snap = _ready_snap()
        _apply(snap, AtrBlock("atr_missing"))
        decision = evaluate(snap, _cfg(), now_s=snap.window_start + 11)
        self.assertEqual(decision.reason, "atr_missing")
        self.assertFalse(decision.ok)


class DelayFilterTest(unittest.TestCase):
    def test_ten_seconds_blocks_and_eleven_allows(self):
        cfg = _cfg()
        snap = _ready_snap()
        with self.assertLogs("pmtrader.strategy", level="INFO") as early:
            blocked = evaluate(snap, cfg, now_s=snap.window_start + 10)
        self.assertEqual(blocked.reason, "too_early")
        self.assertFalse(blocked.ok)
        self.assertTrue(snap.early_cross)
        self.assertEqual(snap.prev_up_mid, 0.50)
        self.assertTrue(any("seconds_since_window_open=10.000" in line for line in early.output))
        self.assertTrue(any("decision=skipped too_early" in line for line in early.output))
        self.assertTrue(any(f"window={snap.slug}" in line for line in early.output))
        self.assertTrue(any("ATR=10.000000" in line and "ratio=1.000000" in line for line in early.output))

        fresh = _ready_snap()
        with self.assertLogs("pmtrader.strategy", level="INFO") as later:
            allowed = evaluate(fresh, cfg, now_s=fresh.window_start + 11)
        self.assertTrue(allowed.ok)
        self.assertEqual(allowed.side, "up")
        self.assertFalse(fresh.early_cross)
        self.assertTrue(any("seconds_since_window_open=11.000" in line for line in later.output))
        self.assertTrue(any("decision=traded" in line for line in later.output))

    def test_early_cross_is_not_released_after_the_delay(self):
        cfg = _cfg()
        snap = _ready_snap()
        first = evaluate(snap, cfg, now_s=snap.window_start + 1)
        self.assertEqual(first.reason, "too_early")

        snap.up_mid = 0.30
        snap.up_ask = 0.31
        evaluate(snap, cfg, now_s=snap.window_start + 12)
        snap.up_mid = 0.50
        snap.up_ask = 0.51
        with self.assertLogs("pmtrader.strategy", level="INFO") as recross:
            again = evaluate(snap, cfg, now_s=snap.window_start + 13)
        self.assertEqual(again.reason, "early_cross")
        self.assertFalse(again.ok)
        self.assertTrue(any("decision=skipped early_cross" in line for line in recross.output))

    def test_ratio_above_cap_skips(self):
        snap = _ready_snap()
        snap.atr_ratio = 1.125
        self.assertTrue(evaluate(snap, _cfg(), now_s=snap.window_start + 11).ok)
        snap = _ready_snap()
        snap.atr_ratio = 1.1250001
        snap.atr_value = 11.250001
        snap.atr_mean = 10.0
        blocked = evaluate(snap, _cfg(), now_s=snap.window_start + 11)
        self.assertEqual(blocked.reason, "atr_high")
        self.assertFalse(blocked.ok)


class ConfigFilterTest(unittest.TestCase):
    def test_config_toml_thresholds(self):
        path = Path(__file__).resolve().parents[1] / "config.toml"
        cfg = load_config(path)
        self.assertEqual(cfg.min_delay_seconds, 10.0)
        self.assertEqual(cfg.atr_mean_max, 1.125)
        self.assertEqual(cfg.stake_usd, 2)
        self.assertEqual(cfg.invert_hours, None)
        self.assertEqual(cfg.odds_min, 0.45)
        self.assertEqual(cfg.odds_max, 0.55)


if __name__ == "__main__":
    unittest.main()
