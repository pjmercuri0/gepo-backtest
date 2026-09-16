"""§0.45 market-gap drift: the drift is off at zero, moves P_real the right way, and the sigma and gap
definitions match their pandas reference implementations."""
import unittest
import numpy as np, pandas as pd
import ent_canon as ec


def closes(n=300, seed=1, tk='AAA'):
    r = np.random.default_rng(seed).normal(0, 0.015, n)
    d = pd.bdate_range('2024-01-01', periods=n)
    return pd.DataFrame({'ticker': tk, 'date': d, 'close': 100 * np.exp(np.cumsum(r))})


def cands(cl, i=280):
    s = float(cl.close.iloc[i]); d = cl.date.iloc[i]
    return pd.DataFrame({'ticker': 'AAA', 'entry_date': [d, d], 'DTE': [2, 2], 'entry_price': [s, s],
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

    def test_sigma_matches_pandas_rolling(self):
        cl = closes(); C = cands(cl, 250)
        m, s, b = ec.gap_fit(C.entry_date.iloc[0].year + 2)                         # any year with a fit
        mg = pd.DataFrame({'date': [C.entry_date.iloc[0]], 'mkt_gap': [m + s]})     # z = 1
        mu = ec.gap_drift(C, cl, mg, gamma=1.0, fit=(m, s, b))
        ref = np.log(cl.close).diff().rolling(ec.GAP_SIGMA_N).std().shift(1).iloc[250]
        np.testing.assert_allclose(mu, b * ref * np.sqrt(2), rtol=1e-9)

    def test_walk_forward_year_lookup(self):
        self.assertEqual(ec.gap_fit(2019)[2], 0.0)                                  # before the first fit: no drift
        self.assertEqual(ec.gap_fit(2026), ec.GAP_FIT[2026])
        self.assertEqual(ec.gap_fit(2030), ec.GAP_FIT[max(ec.GAP_FIT)])             # no refit yet: latest earlier fit

    def test_no_gap_row_means_no_drift(self):
        cl = closes(); C = cands(cl)
        self.assertTrue((ec.gap_drift(C, cl, pd.DataFrame({'date': [pd.Timestamp('1999-01-01')], 'mkt_gap': [1.0]})) == 0).all())

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
