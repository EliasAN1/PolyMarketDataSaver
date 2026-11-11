import unittest

from pmtrader.profile import adapt_data_payload


class DataApiV2Test(unittest.TestCase):
    def test_value_envelope(self):
        got = adapt_data_payload("value", {"data": {"proxy_wallet": "0xabc", "value": 12.5}})
        self.assertEqual(got, {"value": 12.5})

    def test_positions_use_wallet_field_names(self):
        got = adapt_data_payload(
            "positions",
            {"data": [{"current_size": 4, "avg_price": 0.5, "current_price": 0.6, "unrealized_pnl": 0.4, "title": "BTC"}]},
        )
        self.assertEqual(got[0]["size"], 4)
        self.assertEqual(got[0]["avgPrice"], 0.5)
        self.assertEqual(got[0]["curPrice"], 0.6)
        self.assertEqual(got[0]["cashPnl"], 0.4)
        self.assertEqual(got[0]["title"], "BTC")

    def test_activity_usdc_size(self):
        got = adapt_data_payload("activity", {"data": [{"type": "REDEEM", "usdc_size": 3.2}]})
        self.assertEqual(got[0]["usdcSize"], 3.2)

    def test_trades_pass_through_rows(self):
        got = adapt_data_payload("trades", {"data": [{"side": "BUY", "price": 0.46, "size": 10}]})
        self.assertEqual(got, [{"side": "BUY", "price": 0.46, "size": 10}])


if __name__ == "__main__":
    unittest.main()
