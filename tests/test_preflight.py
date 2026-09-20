import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pandas as pd

from live import live_config, preflight
from live.provenance import snapshot_manifest, write_manifest


ET = ZoneInfo("America/New_York")


class PreflightTests(unittest.TestCase):
    def test_market_hours_are_checked_in_eastern_time(self):
        monday_open = datetime(2026, 9, 21, 9, 30, tzinfo=ET)
        monday_before_open = datetime(2026, 9, 21, 9, 29, tzinfo=ET)
        self.assertTrue(preflight._check_market_session(monday_open, False)[0].ok)
        self.assertFalse(preflight._check_market_session(monday_before_open, False)[0].ok)
        self.assertTrue(preflight._check_market_session(monday_before_open, True)[0].ok)

    def test_snapshot_schema_requires_liquidity_fields(self):
        now = datetime(2026, 9, 21, 10, 0, tzinfo=ET)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "snapshot.parquet"
            pd.DataFrame({
                "Symbol": ["AAPL"],
                "DataDate": [now.date()],
                "ExpirationDate": [now.date()],
                "StrikePrice": [100.0],
                "PutCall": ["put"],
                "BidPrice": [1.0],
                "AskPrice": [1.2],
                "AbsDelta": [0.55],
                "UnderlyingPrice": [100.0],
                "ImpliedVolatility": [0.25],
                "OpenInterest": [100],
            }).to_parquet(path, index=False)
            os.utime(path, (now.timestamp(), now.timestamp()))
            with patch.object(live_config, "LIVE_ALLOW_VOLUME_FALLBACK", True):
                checks = preflight._check_snapshot(path, now, allow_stale=False)
            result = {check.name: check for check in checks}
            self.assertFalse(result["snapshot-liquidity-schema"].ok)

    def test_fresh_complete_snapshot_passes(self):
        now = datetime(2026, 9, 21, 10, 0, tzinfo=ET)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "snapshot.parquet"
            pd.DataFrame({
                "Symbol": ["AAPL"], "DataDate": [now.date()],
                "ExpirationDate": [now.date()], "StrikePrice": [100.0],
                "PutCall": ["put"], "BidPrice": [1.0], "AskPrice": [1.2],
                "AbsDelta": [0.55], "UnderlyingPrice": [100.0],
                "ImpliedVolatility": [0.25], "OpenInterest": [100],
                "Volume": [100], "BidSize": [1], "AskSize": [1],
            }).to_parquet(path, index=False)
            os.utime(path, (now.timestamp(), now.timestamp()))
            write_manifest(snapshot_manifest(pd.read_parquet(path), path), path)
            checks = preflight._check_snapshot(path, now, allow_stale=False)
            self.assertTrue(all(check.ok for check in checks))

    def test_feature_policy_is_fail_closed_by_default(self):
        checks = {check.name: check for check in preflight._check_config()}
        self.assertTrue(checks["feature-policy"].ok)


if __name__ == "__main__":
    unittest.main()
