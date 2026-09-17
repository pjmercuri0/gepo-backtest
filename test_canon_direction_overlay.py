"""Fast invariants for the 2026-09-16 bull/parity direction overlay."""
import numpy as np
import pandas as pd

import config
import ent_canon as ec


def _chain():
    rows = []
    for strike, call_iv, put_iv in [(99.0, 0.24, 0.30), (100.0, 0.26, 0.28)]:
        for right, iv, delta in [('call', call_iv, 0.50), ('put', put_iv, -0.50)]:
            rows.append({
                'Symbol': 'XYZ', 'DataDate': pd.Timestamp('2026-09-16'),
                'ExpirationDate': pd.Timestamp('2026-09-18'), 'PutCall': right,
                'StrikePrice': strike, 'ImpliedVolatility': iv, 'Delta': delta,
                'BidPrice': 1.0, 'AskPrice': 1.1,
            })
    return pd.DataFrame(rows)


def test_chain_parity_formula():
    got = ec.chain_parity_signal(_chain()).iloc[0]
    # median[(.24-.30), (.26-.28)] = -.04
    assert np.isclose(got['parity_bull_raw'], -0.04)
    assert got['parity_pairs'] == 2

    # Live snapshots carry AbsDelta rather than signed Delta.
    live = _chain().drop(columns='Delta')
    live['AbsDelta'] = 0.50
    got_live = ec.chain_parity_signal(live).iloc[0]
    assert np.isclose(got_live['parity_bull_raw'], -0.04)


def test_daily_parity_percentile_and_2020_neutral():
    d = pd.DataFrame({
        'entry_date': [pd.Timestamp('2026-09-16')] * 3 + [pd.Timestamp('2020-09-16')],
        'spread_type': ['bull_put'] * 4,
        'parity_bull_raw': [-0.3, 0.0, 0.3, 99.0],
    })
    got = ec.add_parity_percentile(d)
    assert np.allclose(got.loc[:2, 'parity_pct'], [1 / 3, 2 / 3, 1.0])
    assert got.loc[3, 'parity_bull_signed'] == 0.0
    assert got.loc[3, 'parity_pct'] == 0.5


def test_canonical_constants_are_synchronized():
    assert config.GROUND_THRESHOLD == ec.THR == 0.005
    assert config.TOP_N == ec.TOP_N == 10
    assert config.PARITY_MIN_PCT == ec.PARITY_MIN_PCT == 0.12
    assert config.REGIME_FILTER and config.REGIME_BULL_ONLY
    assert config.REGIME_LAG_SESSIONS == 1
    assert config.REGIME_FAIL_CLOSED
    assert config.REGIME_MAX_STALE_CALENDAR_DAYS == 4


if __name__ == '__main__':
    test_chain_parity_formula()
    test_daily_parity_percentile_and_2020_neutral()
    test_canonical_constants_are_synchronized()
    print('canon direction overlay tests passed')
