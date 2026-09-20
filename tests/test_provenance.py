import os
import tempfile
import unittest
from datetime import date
from pathlib import Path

import pandas as pd

from live import provenance


class ProvenanceTests(unittest.TestCase):
    def test_manifest_contains_coverage_and_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "2026-09-21" / "1000.parquet"
            path.parent.mkdir()
            frame = pd.DataFrame({
                "Symbol": ["AAPL", "AAPL"],
                "DataDate": [date(2026, 9, 21)] * 2,
                "ExpirationDate": [date(2026, 9, 25)] * 2,
                "PutCall": ["put", "call"],
                "OpenInterest": [100, 0],
                "Volume": [100, 25],
                "BidSize": [1, 0],
                "AskSize": [1, 1],
            })
            frame.to_parquet(path, index=False)
            manifest = provenance.snapshot_manifest(frame, path)
            self.assertEqual(manifest["row_count"], 2)
            self.assertEqual(manifest["ticker_count"], 1)
            self.assertEqual(manifest["put_rows"], 1)
            self.assertEqual(manifest["call_rows"], 1)
            self.assertEqual(manifest["liquidity"]["oi_positive_fraction"], 0.5)
            self.assertTrue(manifest["config_hash"])
            self.assertEqual(manifest["snapshot_sha256"], provenance.file_sha256(path))

            written = provenance.write_manifest(manifest, path)
            self.assertEqual(provenance.read_manifest(path), manifest)
            self.assertTrue(written.exists())

    def test_config_hash_is_stable(self):
        self.assertEqual(provenance.config_hash(), provenance.config_hash())
        self.assertTrue(provenance.config_hash())


if __name__ == "__main__":
    unittest.main()
