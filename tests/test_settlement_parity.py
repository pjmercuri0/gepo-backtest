"""settle_pnl must agree with the canon vectorised settlement.

The canon haircut lives in research/bear_regime_sweep.py (which writes both
published payloads) as:

    pnl = np.where(win, credit, np.where(loss, -max_loss, partial_pnl)) * 100
    pnl[partial & (pnl > 0)] *= 0.5

History, Actuals and the 15:45 freeze used to call calc_pnl directly and so paid
partial WINS in full, disagreeing with the books they are measured against.
"""
import unittest

import numpy as np

import spreads


def canon_pnl(spot, short, long_, credit, spread_type):
    """Vectorised canon settlement, transcribed from bear_regime_sweep.realize()."""
    bull = spread_type == "bull_put"
    width = abs(short - long_)
    max_loss = width - credit
    win = spot > short if bull else spot < short
    loss = spot <= long_ if bull else spot >= long_
    partial = not (win or loss)
    partial_pnl = credit - (short - spot) if bull else credit - (spot - short)
    pnl = (credit if win else (-max_loss if loss else partial_pnl)) * 100.0
    if partial and pnl > 0:
        pnl *= 0.5
    return pnl


class SettlementParityTests(unittest.TestCase):
    def test_matches_canon_across_the_payoff(self):
        cases = []
        for stype, short, long_ in (("bull_put", 100.0, 97.5), ("bear_call", 100.0, 102.5)):
            for credit in (0.40, 1.00, 1.60):
                for spot in np.arange(94.0, 106.01, 0.25):
                    cases.append((stype, short, long_, credit, round(float(spot), 2)))
        for stype, short, long_, credit, spot in cases:
            width = abs(short - long_)
            got = spreads.settle_pnl(spot, short, long_, credit, width - credit, stype) * 100.0
            want = canon_pnl(spot, short, long_, credit, stype)
            self.assertAlmostEqual(got, want, places=6,
                                   msg=f"{stype} c={credit} spot={spot}")

    def test_haircut_is_one_sided(self):
        # bull put 100/97.5, credit 1.00 -> partial zone is (97.5, 100]
        full = spreads.calc_pnl(99.5, 100.0, 97.5, 1.0, 1.5, "bull_put")
        cut = spreads.settle_pnl(99.5, 100.0, 97.5, 1.0, 1.5, "bull_put")
        self.assertAlmostEqual(cut, full * 0.5)          # partial WIN halved
        # a partial LOSS is untouched
        full_l = spreads.calc_pnl(98.5, 100.0, 97.5, 1.0, 1.5, "bull_put")
        cut_l = spreads.settle_pnl(98.5, 100.0, 97.5, 1.0, 1.5, "bull_put")
        self.assertLess(full_l, 0)
        self.assertAlmostEqual(cut_l, full_l)
        # full win and full loss untouched
        self.assertAlmostEqual(spreads.settle_pnl(101.0, 100.0, 97.5, 1.0, 1.5, "bull_put"), 1.0)
        self.assertAlmostEqual(spreads.settle_pnl(97.0, 100.0, 97.5, 1.0, 1.5, "bull_put"), -1.5)


if __name__ == "__main__":
    unittest.main()
