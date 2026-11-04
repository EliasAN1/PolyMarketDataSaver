"""Today-only day stop / skip-hour overlay."""

import time
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from pmtrader.config import TraderConfig
from pmtrader.dayctrl import DayControl
from pmtrader.snapshot import LiveSnapshot
from pmtrader.strategy import evaluate
from pmtrader.ui.server import create_app


class Clock:
    def __init__(self, value: datetime):
        self.value = value

    def __call__(self) -> datetime:
        return self.value


class DayControlTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "day-control.json"
        self.clock = Clock(datetime(2026, 10, 12, 17, 30))
        self.day = DayControl(self.path, clock=self.clock)

    def test_stop_persists_and_blocks(self):
        payload = self.day.stop()
        self.assertTrue(payload["stopped"])
        self.assertEqual(payload["status"], "Off until midnight")
        self.assertEqual(self.day.entry_block_reason(time.time()), "stopped_for_day")
        restored = DayControl(self.path, clock=self.clock)
        self.assertTrue(restored.payload()["stopped"])

    def test_start_clears_stop_keeps_skipped_hours(self):
        self.day.toggle_hour(13)
        self.day.stop()
        payload = self.day.start()
        self.assertFalse(payload["stopped"])
        self.assertEqual(payload["skip_hours"], [13])
        self.assertEqual(payload["status"], "Trading · skipped 13")

    def test_toggle_hour_on_and_off(self):
        self.day.toggle_hour(14)
        self.assertEqual(self.day.payload()["skip_hours"], [14])
        self.day.toggle_hour(14)
        self.assertEqual(self.day.payload()["skip_hours"], [])

    def test_skipped_hour_blocks_that_window(self):
        self.day.toggle_hour(13)
        window_13 = datetime(2026, 10, 12, 13, 0).timestamp()
        window_15 = datetime(2026, 10, 12, 15, 0).timestamp()
        self.assertEqual(self.day.entry_block_reason(window_13), "skipped_hour")
        self.assertIsNone(self.day.entry_block_reason(window_15))

    def test_new_local_date_drops_the_pause(self):
        self.day.stop()
        self.day.toggle_hour(10)
        self.clock.value = datetime(2026, 10, 13, 0, 1)
        payload = self.day.payload()
        self.assertFalse(payload["stopped"])
        self.assertEqual(payload["skip_hours"], [])
        self.assertEqual(payload["date"], "2026-10-13")

    def test_tomorrow_skip_does_not_block_today(self):
        self.day.toggle_hour(13, "tomorrow")
        self.day.stop("tomorrow")
        window_13 = datetime(2026, 10, 12, 13, 0).timestamp()
        self.assertIsNone(self.day.entry_block_reason(window_13))
        payload = self.day.payload()
        self.assertFalse(payload["stopped"])
        self.assertTrue(payload["tomorrow"]["stopped"])
        self.assertEqual(payload["tomorrow"]["skip_hours"], [13])
        self.assertEqual(payload["tomorrow"]["date"], "2026-10-13")

    def test_tomorrow_overlay_becomes_today_at_midnight(self):
        self.day.toggle_hour(10, "tomorrow")
        self.day.stop("tomorrow")
        self.clock.value = datetime(2026, 10, 13, 0, 1)
        payload = self.day.payload()
        self.assertTrue(payload["stopped"])
        self.assertEqual(payload["skip_hours"], [10])
        self.assertEqual(payload["date"], "2026-10-13")
        self.assertFalse(payload["tomorrow"]["stopped"])
        self.assertEqual(payload["tomorrow"]["skip_hours"], [])
        self.assertEqual(self.day.entry_block_reason(datetime(2026, 10, 13, 10, 0).timestamp()), "stopped_for_day")


class StrategyDayGateTest(unittest.TestCase):
    def _snap(self, now: float) -> LiveSnapshot:
        return LiveSnapshot(window_start=int(now - 10), window_end=int(now + 290))

    def test_stopped_for_day_skips_before_other_filters(self):
        now = time.time()
        class Stopped:
            def entry_block_reason(self, _ts):
                return "stopped_for_day"
        decision = evaluate(
            self._snap(now),
            TraderConfig(use_btc_distance=False, use_twap=False, use_venues=False),
            now_s=now,
            day=Stopped(),
        )
        self.assertEqual(decision.reason, "stopped_for_day")
        self.assertFalse(decision.ok)

    def test_skipped_hour_skips_before_other_filters(self):
        now = time.time()
        class Skipped:
            def entry_block_reason(self, _ts):
                return "skipped_hour"
        decision = evaluate(
            self._snap(now),
            TraderConfig(use_btc_distance=False, use_twap=False, use_venues=False),
            now_s=now,
            day=Skipped(),
        )
        self.assertEqual(decision.reason, "skipped_hour")


class DayApiTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        path = Path(self.temp.name) / "day-control.json"
        self.day = DayControl(path, clock=Clock(datetime(2026, 10, 12, 17, 30)))

        class Trader:
            pass

        trader = Trader()
        trader.day = self.day
        self.client = TestClient(create_app(log_path=Path(self.temp.name) / "trades.jsonl", trader=trader))
        self.addCleanup(self.client.close)

    def test_stop_start_and_toggle_hour(self):
        stopped = self.client.post("/api/day", json={"action": "stop"}).json()
        self.assertTrue(stopped["stopped"])
        toggled = self.client.post("/api/day", json={"action": "toggle_hour", "hour": 15}).json()
        self.assertEqual(toggled["skip_hours"], [15])
        started = self.client.post("/api/day", json={"action": "start"}).json()
        self.assertFalse(started["stopped"])
        self.assertEqual(started["skip_hours"], [15])
        tomorrow = self.client.post("/api/day", json={"action": "toggle_hour", "hour": 17, "when": "tomorrow"}).json()
        self.assertEqual(tomorrow["tomorrow"]["skip_hours"], [17])
        self.assertEqual(tomorrow["skip_hours"], [15])

    def test_unknown_action_is_rejected(self):
        self.assertEqual(self.client.post("/api/day", json={"action": "explode"}).status_code, 400)

    def test_live_includes_day_overlay(self):
        with patch("pmtrader.ui.server.live_payload", return_value={"running": True, "day": self.day.payload()}):
            payload = self.client.get("/api/live").json()
        self.assertIn("day", payload)
        self.assertFalse(payload["day"]["stopped"])


if __name__ == "__main__":
    unittest.main()
