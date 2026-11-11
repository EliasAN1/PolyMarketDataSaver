import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from pmtrader.when import session_catalog


BERLIN = ZoneInfo("Europe/Berlin")


def labels(moment: datetime) -> dict[str, str]:
    return {row["key"]: row["local"].removesuffix(" local") for row in session_catalog(moment)}


class SessionHourTest(unittest.TestCase):
    def test_winter_berlin_shows_the_real_market_clock(self):
        # 6 Nov 2026, Europe and New York are both on standard time.
        got = labels(datetime(2026, 11, 6, 12, tzinfo=BERLIN))
        self.assertEqual(got["tokyo_open"], "01:00–03:30")
        self.assertEqual(got["london_open"], "09:00–12:00")
        self.assertEqual(got["wall_open"], "15:30–18:30")
        self.assertEqual(got["asia"], "01:00–09:00")
        self.assertEqual(got["london"], "09:00–17:30")
        self.assertEqual(got["wall"], "15:30–22:00")
        self.assertEqual(got["overlap"], "15:30–17:30")
        self.assertEqual(got["off"], "22:00–01:00")

    def test_summer_berlin_keeps_london_and_new_york(self):
        got = labels(datetime(2026, 7, 15, 12, tzinfo=BERLIN))
        self.assertEqual(got["london"], "09:00–17:30")
        self.assertEqual(got["london_open"], "09:00–12:00")
        self.assertEqual(got["wall"], "15:30–22:00")
        self.assertEqual(got["wall_open"], "15:30–18:30")
        self.assertEqual(got["overlap"], "15:30–17:30")
        self.assertEqual(got["tokyo_open"], "02:00–04:30")
        self.assertEqual(got["off"], "22:00–02:00")


if __name__ == "__main__":
    unittest.main()
