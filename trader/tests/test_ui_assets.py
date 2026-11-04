"""The compact UI stylesheet must be reachable through the real ASGI app."""
from pathlib import Path
import tempfile
import unittest

from fastapi.testclient import TestClient
from pmtrader.ui.server import create_app


class UiAssetsTest(unittest.TestCase):
    def test_compact_assets_are_served(self):
        with tempfile.TemporaryDirectory() as directory:
            with TestClient(create_app(log_path=Path(directory) / "trades.jsonl")) as client:
                index = client.get("/")
                self.assertIn('/compact.css?', index.text)
                for url, content_type in [('/compact.css?v=23', 'text/css'), ('/js/compact-ui.js?v=23', 'javascript')]:
                    response = client.get(url)
                    self.assertEqual(response.status_code, 200)
                    self.assertIn(content_type, response.headers['content-type'])
                    self.assertIn('no-store', response.headers['cache-control'])


if __name__ == '__main__':
    unittest.main()
