"""§0.45 own-gap drift: off at zero, moves P_real the right way, uses only the candidate's own gap, and the sigma
and gap definitions match their pandas reference implementations."""
import unittest
import numpy as np, pandas as pd
import ent_canon as ec


def closes(n=300, seed=1, tk='AAA'):
    r = np.random.default_rng(seed).normal(0, 0.015, n)
    d = pd.bdate_range('2024-01-01', periods=n)
    return pd.DataFrame({'ticker': tk, 'date': d, 'close': 100 * np.exp(np.cumsum(r))})


def cands(cl, i=280, tk='AAA'):
    s = float(cl.close.iloc[i]); d = cl.date.iloc[i]
    return pd.DataFrame({'ticker': tk, 'entry_date': [d, d], 'DTE': [2, 2], 'entry_price': [s, s],
                         'spread_type': ['bull_put', 'bear_call'], 'short_strike': [s * 0.995, s * 1.005], 'long_strike': [s * 0.975, s * 1.025]})


class GapDrift(unittest.TestCase):
    def test_zero_drift_is_canon(self):
        cl = closes(); C = cands(cl)
        np.testing.assert_array_equal(ec.p_real(C, cl), ec.p_real(C, cl, mu=np.zeros(2)))
        np.testing.assert_array_equal(ec.p_real(C, cl), ec.p_real(C, cl, mu=None))

    def test_up_drift_helps_puts_hurts_calls(self):
        cl = closes(); C = cands(cl)
        p0, p1 = ec.p_real(C, cl), ec.p_real(C, cl, mu=np.full(2, 0.01))
        self.assertGreater(p1[0, 0], p0[0, 0]); self.assertLess(p1[1, 0], p0[1, 0])

    def test_sigma_and_drift_match_reference(self):
        cl = closes(); C = cands(cl, 250); d = C.entry_date.iloc[0]
        m, s, b = 0.01, 0.4, 0.03
        gaps = pd.DataFrame({'ticker': ['AAA'], 'date': [d], 'gap': [m + s]})                 # z = 1
        mu = ec.gap_drift(C, cl, gaps, gamma=1.0, fit=(m, s, b))
        ref = np.log(cl.close).diff().rolling(ec.GAP_SIGMA_N).std().shift(1).iloc[250]
        np.testing.assert_allclose(mu, b * ref * np.sqrt(2), rtol=1e-9)

    def test_only_own_gap_is_used(self):
        cl = pd.concat([closes(tk='AAA'), closes(seed=2, tk='BBB')]); C = cands(closes(), 250); d = C.entry_date.iloc[0]
        other = pd.DataFrame({'ticker': ['BBB'], 'date': [d], 'gap': [3.0]})                   # another stock gapped hard
        self.assertTrue((ec.gap_drift(C, cl, other, gamma=1.0, fit=(0.0, 0.4, 0.05)) == 0).all())

    def test_walk_forward_year_lookup(self):
        self.assertEqual(ec.gap_fit(2019)[2], 0.0)
        self.assertEqual(ec.gap_fit(2026), ec.GAP_FIT[2026])
        self.assertEqual(ec.gap_fit(2030), ec.GAP_FIT[max(ec.GAP_FIT)])

    def test_gap_definition(self):
        n = 120; rng = np.random.default_rng(3); c = 50 * np.exp(np.cumsum(rng.normal(0, .01, n)))
        o = c * (1 + rng.normal(0, .005, n)); h = np.maximum(o, c) * 1.01; l = np.minimum(o, c) * 0.99
        b = pd.DataFrame({'ticker': 'AAA', 'date': pd.bdate_range('2024-01-01', periods=n), 'open': o, 'high': h, 'low': l, 'close': c})
        g = ec.name_gaps(b).gap.values
        cs, hs, ls, os_ = map(pd.Series, (c, h, l, o))
        tr = pd.concat([hs - ls, (hs - cs.shift()).abs(), (ls - cs.shift()).abs()], axis=1).max(axis=1)
        atr = tr.ewm(alpha=1 / 14, adjust=False).mean()
        ref = ((os_ / cs.shift() - 1) / (atr.shift() / cs.shift())).values; ref[:60] = np.nan
        np.testing.assert_allclose(g, ref, equal_nan=True)


if __name__ == '__main__':
    unittest.main()
