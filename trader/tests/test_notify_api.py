"""Notification reconnect coverage; uses temporary logs, never the live trader."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from pmtrader.ui.server import create_app


class NotifyApiTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.log = Path(self.temp.name) / "trades.jsonl"
        self.client = TestClient(create_app(log_path=self.log))
        self.addCleanup(self.client.close)

    def write_rows(self, rows):
        self.log.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

    def rows(self, count):
        return [dict(event="resolve", order_id=f"order-{i}", ts=1000 + i,
                     net_pnl_usd=2 if i % 2 else -1, won=bool(i % 2),
                     slug="btc-updown-5m-1790300000") for i in range(count)]

    def test_reconnect_returns_entire_gap_and_correct_total(self):
        self.write_rows(self.rows(700))
        events = self.client.get("/api/notify?since=1000").json()["events"]
        self.assertEqual(len(events), 700)
        self.assertEqual(sum(row["net_pnl_usd"] for row in events), 350)

    def test_first_connection_keeps_legacy_history_limit(self):
        self.write_rows(self.rows(700))
        self.assertEqual(len(self.client.get("/api/notify").json()["events"]), 200)

    def test_cursor_is_inclusive_and_event_ids_are_stable(self):
        self.write_rows(self.rows(5))
        first = self.client.get("/api/notify?since=1002").json()["events"]
        second = self.client.get("/api/notify?since=1002").json()["events"]
        self.assertEqual(first, second)
        self.assertEqual([row["ts"] for row in first], [1002, 1003, 1004])

    def test_snapshot_cursor_is_server_time(self):
        self.write_rows([])
        with patch("pmtrader.ui.server.time.time", return_value=1790300000):
            payload = self.client.get("/api/notify?since=1000").json()
        self.assertEqual(payload["server_ts"], 1790300000)
        self.assertEqual(payload["events"], [])

    def test_non_trade_rows_do_not_hide_older_results(self):
        self.write_rows(self.rows(1) + [dict(event="reject", ts=2000 + i) for i in range(300)])
        events = self.client.get("/api/notify?since=1000").json()["events"]
        self.assertEqual(len(events), 1)

    def test_negative_cursor_is_rejected(self):
        self.assertEqual(self.client.get("/api/notify?since=-1").status_code, 422)

    def test_resolve_inherits_entry_price_and_stake(self):
        self.write_rows([
            {
                "event": "entry",
                "order_id": "order-xyz",
                "slug": "btc-updown-5m-1790300000",
                "side": "UP",
                "fill_price": 0.52,
                "stake_usd": 50.0,
                "ts": 1000,
                "window_end_ts": 1790300300,
            },
            {
                "event": "resolve",
                "order_id": "order-xyz",
                "slug": "btc-updown-5m-1790300000",
                "won": True,
                "net_pnl_usd": 42.50,
                "ts": 1050,
            },
        ])
        events = self.client.get("/api/notify?since=1020").json()["events"]
        self.assertEqual(len(events), 1)
        res = events[0]
        self.assertEqual(res["event"], "resolve")
        self.assertEqual(res["side"], "UP")
        self.assertEqual(res["fill_price"], 0.52)
        self.assertEqual(res["stake_usd"], 50.0)
        self.assertEqual(res["window_start_ts"], 1790300000)
        self.assertEqual(res["window_end_ts"], 1790300300)



if __name__ == "__main__":
    unittest.main()
