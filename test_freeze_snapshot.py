import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from live import live_config
from live.freeze_snapshot import maybe_freeze


def _pick(ticker, short=100.0, long=99.0):
    return {
        "ticker": ticker,
        "spread_type": "bull_put",
        "expiry_date": "2026-08-28T00:00:00",
        "short_strike": short,
        "long_strike": long,
    }


class FreezeSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.frozen_dir = self.root / "frozen"
        self.ranked_dir = self.root / "ranked"
        self.frozen_dir.mkdir()
        self.ranked_dir.mkdir()
        self.old_frozen = live_config.FROZEN_DIR
        self.old_ranked = live_config.RANKED_DIR
        live_config.FROZEN_DIR = str(self.frozen_dir)
        live_config.RANKED_DIR = str(self.ranked_dir)

    def tearDown(self):
        live_config.FROZEN_DIR = self.old_frozen
        live_config.RANKED_DIR = self.old_ranked
        self.tmp.cleanup()

    def _latest(self, hhmm, picks):
        path = self.ranked_dir / "latest.json"
        path.write_text(json.dumps({
            "snapshot_file": f"live/snapshots/2026-08-25/{hhmm}.parquet",
            "snapshot_ts": f"2026-08-25T{hhmm[:2]}:{hhmm[2:]}:45",
            "top_picks": picks,
        }))
        return path

    def test_1531_topup_preserves_original_and_uses_established_metadata(self):
        original = _pick("NEE")
        latest = self._latest("1501", [original])
        maybe_freeze(latest, datetime(2026, 8, 25, 15, 2, 45))
        frozen = self.frozen_dir / "2026-08-25.json"
        payload = json.loads(frozen.read_text())
        payload["tracking"] = {"NEE": [{"mark": 0.2}]}
        frozen.write_text(json.dumps(payload))

        latest = self._latest("1531", [original, _pick("GS"), _pick("COST")])
        maybe_freeze(latest, datetime(2026, 8, 25, 15, 40, 0))
        result = json.loads(frozen.read_text())

        self.assertEqual([p["ticker"] for p in result["top_picks"]], ["NEE", "GS", "COST"])
        self.assertNotIn("freeze_added_at", result["top_picks"][0])
        self.assertEqual(result["top_picks"][1]["freeze_added_at"], "15:31")
        self.assertEqual(result["top_picks"][2]["freeze_added_at"], "15:31")
        self.assertEqual(result["frozen_at"], "15:01+15:31")
        self.assertEqual(result["freeze_topup_original_count"], 1)
        self.assertEqual(result["freeze_topup_added_count"], 2)
        self.assertEqual(result["tracking"], {"NEE": [{"mark": 0.2}]})

        maybe_freeze(latest, datetime(2026, 8, 25, 15, 45, 0))
        self.assertEqual(len(json.loads(frozen.read_text())["top_picks"]), 3)


if __name__ == "__main__":
    unittest.main()
