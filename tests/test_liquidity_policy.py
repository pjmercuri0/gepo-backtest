import unittest

import pandas as pd

import config
import spreads


class LiquidityPolicyTests(unittest.TestCase):
    def setUp(self):
        self.original = {
            name: getattr(config, name, None)
            for name in (
                "MIN_OPEN_INTEREST",
                "ALLOW_VOLUME_LIQUIDITY_FALLBACK",
                "MIN_LIQUIDITY_VOLUME",
                "MIN_LIQUIDITY_BBO_SIZE",
            )
        }
        config.MIN_OPEN_INTEREST = 100
        config.ALLOW_VOLUME_LIQUIDITY_FALLBACK = True
        config.MIN_LIQUIDITY_VOLUME = 100
        config.MIN_LIQUIDITY_BBO_SIZE = 1

    def tearDown(self):
        for name, value in self.original.items():
            if value is None:
                delattr(config, name)
            else:
                setattr(config, name, value)

    def liquid(self, **changes):
        row = {
            "OpenInterest": 0,
            "Volume": 100,
            "BidSize": 1,
            "AskSize": 1,
        }
        row.update(changes)
        return pd.Series(row)

    def test_oi_qualified_leg_passes_without_intraday_volume(self):
        self.assertTrue(spreads.is_liquid_row(self.liquid(OpenInterest=100, Volume=0,
                                                          BidSize=0, AskSize=0)))

    def test_missing_oi_uses_volume_and_bbo_fallback(self):
        self.assertTrue(spreads.is_liquid_row(self.liquid(OpenInterest=0)))

    def test_missing_oi_with_insufficient_volume_fails(self):
        self.assertFalse(spreads.is_liquid_row(self.liquid(OpenInterest=0, Volume=99)))

    def test_missing_oi_with_missing_displayed_depth_fails(self):
        self.assertFalse(spreads.is_liquid_row(self.liquid(OpenInterest=0, BidSize=0)))

    def test_disabled_fallback_fails_when_oi_is_missing(self):
        config.ALLOW_VOLUME_LIQUIDITY_FALLBACK = False
        self.assertFalse(spreads.is_liquid_row(self.liquid(OpenInterest=0)))


if __name__ == "__main__":
    unittest.main()
