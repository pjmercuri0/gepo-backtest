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

    def test_config_hash_is_stable_across_processes(self):
        """Same-process equality is not enough: PYTHONHASHSEED is randomised per
        process, so a set-valued config constant used to produce a different
        hash in the ranker than in the fetcher and fail the manifest check."""
        import subprocess
        import sys

        root = Path(__file__).resolve().parents[1]
        snippet = (
            "import sys; sys.path.insert(0, %r)\n"
            "from live.provenance import config_hash; print(config_hash())" % str(root)
        )
        hashes = set()
        for _ in range(4):
            out = subprocess.run([sys.executable, "-c", snippet], capture_output=True,
                                 text=True, cwd=str(root), timeout=120)
            self.assertEqual(out.returncode, 0, out.stderr[-500:])
            hashes.add(out.stdout.strip().splitlines()[-1])
        self.assertEqual(len(hashes), 1, f"config_hash() varies across processes: {hashes}")

    def test_config_hash_ignores_invocation_style(self):
        """DATA_DIR/OUTPUT_DIR are derived from __file__; an unresolved path made
        the hash depend on whether sys.path[0] was relative or absolute."""
        import subprocess
        import sys

        root = Path(__file__).resolve().parents[1]
        hashes = set()
        for entry in (".", str(root)):
            snippet = (
                "import sys; sys.path.insert(0, %r)\n"
                "from live.provenance import config_hash; print(config_hash())" % entry
            )
            out = subprocess.run([sys.executable, "-c", snippet], capture_output=True,
                                 text=True, cwd=str(root), timeout=120)
            self.assertEqual(out.returncode, 0, out.stderr[-500:])
            hashes.add(out.stdout.strip().splitlines()[-1])
        self.assertEqual(len(hashes), 1, f"config_hash() depends on sys.path form: {hashes}")


if __name__ == "__main__":
    unittest.main()
