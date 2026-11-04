"""Preview backups of trades.jsonl without changing the live log path."""
from pathlib import Path
import tempfile
import unittest

from fastapi.testclient import TestClient
from pmtrader.ui.server import archive_label, create_app, resolve_log_source


class LogSourcesTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.dir = Path(self.temp.name)
        self.live = self.dir / "trades.jsonl"
        self.backup = self.dir / "trades-backup-2026-10-20.jsonl"
        self.live.write_text('{"event":"entry","order_id":"live-1"}\n', encoding="utf-8")
        self.backup.write_text('{"event":"entry","order_id":"old-1"}\n', encoding="utf-8")
        self.client = TestClient(create_app(log_path=self.live))

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    def test_archive_label(self):
        self.assertEqual(archive_label("trades-backup-2026-10-20.jsonl"), "Backup 20 Oct 2026")

    def test_lists_live_and_backup(self):
        payload = self.client.get("/api/logs").json()
        self.assertEqual(payload["live"], "trades.jsonl")
        ids = [row["id"] for row in payload["files"]]
        self.assertEqual(ids[0], "live")
        self.assertIn("trades-backup-2026-10-20.jsonl", ids)

    def test_live_log_is_default(self):
        body = self.client.get("/api/logs/trades.jsonl").text
        self.assertIn("live-1", body)
        self.assertNotIn("old-1", body)

    def test_backup_preview(self):
        body = self.client.get("/api/logs/trades.jsonl", params={"source": self.backup.name}).text
        self.assertIn("old-1", body)
        self.assertNotIn("live-1", body)

    def test_rejects_path_escape(self):
        with self.assertRaises(ValueError):
            resolve_log_source(self.live, "../secret.jsonl")
        response = self.client.get("/api/logs/trades.jsonl", params={"source": "../trades.jsonl"})
        self.assertEqual(response.status_code, 400)

    def test_missing_archive_is_404(self):
        response = self.client.get(
            "/api/logs/trades.jsonl",
            params={"source": "trades-backup-1999-01-01.jsonl"},
        )
        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
