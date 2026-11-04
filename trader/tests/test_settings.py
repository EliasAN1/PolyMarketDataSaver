"""Saved schedule, stake, and hours that buy against the signal."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from pmtrader.config import TraderConfig, persist_settings
from pmtrader.snapshot import LiveSnapshot
from pmtrader.strategy import evaluate
from pmtrader.ui.server import create_app


def _ready_snap(window_start: int) -> LiveSnapshot:
    return LiveSnapshot(
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


def _window_at(hour: int) -> int:
    moment = datetime.now().replace(minute=10, second=0, microsecond=0)
    moment += timedelta(hours=(hour - moment.hour) % 24)
    return int(moment.timestamp())


def _signal(hour: int, *, invert: tuple[int, ...] | None = None, down_ask: float = 0.48):
    start = _window_at(hour)
    snap = _ready_snap(start)
    snap.down_ask = down_ask
    cfg = _cfg()
    cfg.invert_hours = invert
    return evaluate(snap, cfg, now_s=start + 30)


class InvertHoursTest(unittest.TestCase):
    def test_plain_hour_buys_the_signal(self):
        decision = _signal(15)
        self.assertTrue(decision.ok)
        self.assertEqual(decision.side, "up")
        self.assertFalse(decision.inverted)

    def test_inverted_hour_buys_the_other_side(self):
        hour = datetime.fromtimestamp(_window_at(15)).hour
        decision = _signal(hour, invert=(hour,), down_ask=0.48)
        self.assertTrue(decision.ok)
        self.assertEqual(decision.side, "down")
        self.assertTrue(decision.inverted)
        self.assertAlmostEqual(decision.ask, 0.48)

    def test_inverted_hour_skips_when_the_other_ask_is_above_the_cap(self):
        hour = datetime.fromtimestamp(_window_at(16)).hour
        decision = _signal(hour, invert=(hour,), down_ask=0.70)
        self.assertFalse(decision.ok)
        self.assertEqual(decision.reason, "invert_ask")
        self.assertIsNone(decision.side)
        self.assertTrue(decision.inverted)

    def test_other_hours_stay_with_the_signal(self):
        hour = datetime.fromtimestamp(_window_at(9)).hour
        other = (hour + 1) % 24
        decision = _signal(hour, invert=(other,), down_ask=0.48)
        self.assertTrue(decision.ok)
        self.assertEqual(decision.side, "up")
        self.assertFalse(decision.inverted)


class SettingsApiTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "config.toml"
        self.path.write_text(
            "stake_usd = 1\nhours = []\nweekdays = []\nsessions = []\ninvert_hours = []\n",
            encoding="utf-8",
        )
        self.cfg = TraderConfig(
            elapsed_from_min=0,
            elapsed_to_min=2,
            odds_min=0.45,
            odds_max=0.55,
            fak_limit=0.60,
            use_btc_distance=False,
            use_twap=False,
            use_venues=False,
            stake_usd=1,
        )

        class Trader:
            pass

        trader = Trader()
        trader.cfg = self.cfg
        trader.config_path = self.path
        self.client = TestClient(create_app(log_path=Path(self.temp.name) / "trades.jsonl", trader=trader))
        self.addCleanup(self.client.close)

    def test_post_updates_memory_and_file(self):
        response = self.client.post(
            "/api/settings",
            json={
                "stake_usd": 2,
                "hours": [9, 10],
                "weekdays": ["Mon"],
                "sessions": ["london"],
                "invert_hours": [15],
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.cfg.stake_usd, 2)
        self.assertEqual(self.cfg.hours, (9, 10))
        self.assertEqual(self.cfg.weekdays, (1,))
        self.assertEqual(self.cfg.sessions, ("london",))
        self.assertEqual(self.cfg.invert_hours, (15,))
        text = self.path.read_text(encoding="utf-8")
        self.assertIn("stake_usd = 2", text)
        self.assertIn("hours = [9, 10]", text)
        self.assertIn('weekdays = ["Mon"]', text)
        self.assertIn('sessions = ["london"]', text)
        self.assertIn("invert_hours = [15]", text)
        self.assertEqual(response.json()["stake_usd"], 2)

    def test_bad_stake_or_hour_is_rejected(self):
        bad_stake = self.client.post(
            "/api/settings",
            json={"stake_usd": 0, "hours": [], "weekdays": [], "sessions": [], "invert_hours": []},
        )
        self.assertEqual(bad_stake.status_code, 400)
        self.assertEqual(self.cfg.stake_usd, 1)
        bad_hour = self.client.post(
            "/api/settings",
            json={"stake_usd": 2.5, "hours": [24], "weekdays": [], "sessions": [], "invert_hours": []},
        )
        self.assertEqual(bad_hour.status_code, 400)
        self.assertEqual(self.cfg.stake_usd, 1)
        self.assertNotIn("stake_usd = 2.5", self.path.read_text(encoding="utf-8"))

    def test_persist_keeps_unrelated_lines(self):
        self.path.write_text(self.path.read_text(encoding="utf-8") + "fak_limit = 0.60\n", encoding="utf-8")
        self.cfg.stake_usd = 2
        persist_settings(self.path, self.cfg)
        text = self.path.read_text(encoding="utf-8")
        self.assertIn("fak_limit = 0.60", text)
        self.assertIn("stake_usd = 2", text)


if __name__ == "__main__":
    unittest.main()
