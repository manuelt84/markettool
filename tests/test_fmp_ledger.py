import os
import unittest

from markettool.infra.fmp.ledger import (
    fmp_context,
    get_fmp_ledger_summary,
    get_fmp_usage_policy,
    record_fmp_call,
)


class FmpLedgerTests(unittest.TestCase):
    def setUp(self):
        os.environ["FMP_LEDGER_ENABLED"] = "true"
        os.environ["FMP_LEDGER_REDIS_URL"] = ""
        os.environ["FMP_USAGE_UNITS_ASSET_ANALYSIS_FULL"] = "5"

    def test_records_usage_kind_and_billable_units(self):
        with fmp_context(usage_kind="asset_analysis_full", symbol="AAPL", user_id="u1"):
            record_fmp_call(
                url="https://financialmodelingprep.com/api/v3/historical-chart/1min/AAPL?apikey=secret",
                status_code=200,
                elapsed_ms=10,
                response_bytes=100,
                rows=12,
            )

        summary = get_fmp_ledger_summary(limit_recent=1)
        recent = summary["recent"][0]

        self.assertEqual(recent["usage_kind"], "asset_analysis_full")
        self.assertEqual(recent["billable_units"], 5)
        self.assertEqual(recent["symbol"], "AAPL")

    def test_empty_response_has_refund_reason(self):
        with fmp_context(usage_kind="asset_analysis_full", symbol="MSFT"):
            record_fmp_call(
                url="https://financialmodelingprep.com/api/v3/historical-chart/1min/MSFT?apikey=secret",
                status_code=200,
                elapsed_ms=10,
                response_bytes=2,
                rows=0,
            )

        summary = get_fmp_ledger_summary(limit_recent=1)
        recent = summary["recent"][0]

        self.assertEqual(recent["billable_units"], 0)
        self.assertEqual(recent["refund_reason"], "empty_response")

    def test_usage_policy_exposes_active_env_units(self):
        os.environ["FMP_USAGE_UNITS_HISTORICAL_BACKFILL"] = "0"
        os.environ["FMP_USAGE_UNITS_UNKNOWN"] = "3"

        policy = get_fmp_usage_policy()

        self.assertTrue(policy["enabled"])
        self.assertEqual(policy["usage_kinds"]["historical_backfill"]["active_units"], 0)
        self.assertEqual(policy["usage_kinds"]["unknown"]["active_units"], 3)
        self.assertNotIn("apikey", str(policy).lower())


if __name__ == "__main__":
    unittest.main()
