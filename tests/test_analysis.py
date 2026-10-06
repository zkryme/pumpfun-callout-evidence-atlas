import unittest

from pump_analyzer.analysis import freshness_class, slot_from_trade, timing_class
from pump_analyzer.clients import SolanaRpcClient, timestamp_ms


class AnalysisHelpersTest(unittest.TestCase):
    def test_timestamp_units(self):
        self.assertEqual(timestamp_ms(1_790_268_374), 1_790_268_374_000)
        self.assertEqual(timestamp_ms(1_790_268_374_000), 1_790_268_374_000)
        self.assertEqual(timestamp_ms("2026-09-24T16:46:14Z"), 1_790_268_374_000)

    def test_slot_from_trade(self):
        self.assertEqual(slot_from_trade({"slotIndexId": "0004500870420012340000"}), 450087042)
        self.assertIsNone(slot_from_trade({}))

    def test_timing_boundaries(self):
        self.assertEqual(timing_class(-5.01, 5), "PRE_CALLOUT")
        self.assertEqual(timing_class(-5, 5), "CALL_WINDOW")
        self.assertEqual(timing_class(5, 5), "CALL_WINDOW")
        self.assertEqual(timing_class(5.01, 5), "POST_CALLOUT")

    def test_freshness(self):
        self.assertEqual(freshness_class(None), "UNKNOWN")
        self.assertEqual(freshness_class(599), "BRAND_NEW")
        self.assertEqual(freshness_class(600), "VERY_FRESH")
        self.assertEqual(freshness_class(86400), "ESTABLISHED")

    def test_signature_history_reports_complete(self):
        client = object.__new__(SolanaRpcClient)
        client.get_signatures_for_address = lambda *args, **kwargs: [
            {"signature": "new", "err": None}, {"signature": "old", "err": None}
        ]
        history, complete = client.get_signature_history_before("wallet", "buy", page_size=3)
        self.assertTrue(complete)
        self.assertEqual([item["signature"] for item in history], ["new", "old"])

    def test_signature_history_reports_truncated(self):
        client = object.__new__(SolanaRpcClient)
        client.get_signatures_for_address = lambda *args, **kwargs: [
            {"signature": "a", "err": None}, {"signature": "b", "err": None}
        ]
        history, complete = client.get_signature_history_before(
            "wallet", "buy", max_pages=1, page_size=2
        )
        self.assertFalse(complete)
        self.assertEqual(len(history), 2)

    def test_helius_first_inbound_sol_transfer(self):
        client = object.__new__(SolanaRpcClient)
        client.call = lambda method, params: {"data": [{
            "signature": "sig", "blockTime": 100, "slot": 9,
            "fromUserAccount": "funder", "toUserAccount": "buyer",
            "amount": "3000000", "decimals": 9,
        }]}
        result = client.get_first_inbound_sol_transfer("buyer", 101_000)
        self.assertEqual(result["funder"], "funder")
        self.assertEqual(result["amount_sol"], 0.003)
        self.assertEqual(result["timestamp"], 100_000)


if __name__ == "__main__":
    unittest.main()
