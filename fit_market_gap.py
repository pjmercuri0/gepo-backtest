"""Fit the §0.45 market-gap drift for entry year Y on every IS stock-day BEFORE Y (walk-forward). Run each January
and paste the printed line into ent_canon.GAP_FIT, so the new year trades on a fit that saw only earlier data.

    python3 fit_market_gap.py 2027                 # one year
    python3 fit_market_gap.py 2021 2022 ... 2026   # several

Training rows: one row per (ticker, entry date, DTE) among research-frame candidates with a finite Kelly EV and a
positive max loss at the canon fill, entered before Y (frame windows IS and OOT both count once dated before Y).
Target: log(expiry close / entry price) / (sigma_d * sqrt(clip(DTE,1,4))), clipped to +-5. Regressor: the
date's market gap (output/market_gap_backtest.parquet), z-scored on the training rows and clipped to +-4.
beta = no-intercept OLS slope of the centred target on z.
"""
import sys
import numpy as np, pandas as pd
sys.path.insert(0, '.')
import ent_canon as ec, report_ent_canon as rec

def training_frame():
    C = pd.read_parquet(rec.FRAME, columns=['ticker', 'entry_date', 'DTE', 'spread_type', 'entry_price', 'short_strike', 'long_strike',
                                            'model_credit', 'D_ent', 'expiry_close', 'win'])
    C['entry_date'] = pd.to_datetime(C.entry_date).dt.normalize()
    C['width'] = (C.short_strike - C.long_strike).abs()
    CL = ec.backtest_closes()
    P = ec.p_real(C, CL)
    b = C.model_credit.values / (C.width.values - C.model_credit.values)
    _, ell = ec.kelly(np.nan_to_num(P[:, 0]), np.nan_to_num(P[:, 1]), np.nan_to_num(P[:, 2]), b); ell[~np.isfinite(P[:, 0])] = np.nan
    C = C[np.isfinite(ell)].reset_index(drop=True)
    C = C[(C.width - (C.model_credit * ec.FILL_MULT).round(4)).round(4) > 0].reset_index(drop=True)
    C = C.drop_duplicates(['ticker', 'entry_date', 'DTE']).reset_index(drop=True)
    unit = ec.gap_drift(C, CL, pd.DataFrame({'date': C.entry_date.unique(), 'mkt_gap': 0.0}), gamma=1.0, fit=(-1.0, 1.0, 1.0))  # sigma*sqrt(d)
    C['y'] = np.clip(np.log(C.expiry_close / C.entry_price) / unit, -5, 5)
    mg = pd.read_parquet(rec.GAP_SERIES).set_index('date').mkt_gap
    C['x'] = C.entry_date.map(mg)
    return C

def fit(C, year):
    t = C[(C.entry_date.dt.year < year) & np.isfinite(C.y) & np.isfinite(C.x)]
    m, s = float(t.x.mean()), float(t.x.std(ddof=0)); z = np.clip((t.x - m) / s, -4, 4).values; yc = (t.y - t.y.mean()).values
    return m, s, float(z @ yc / (z @ z)), len(t)

if __name__ == '__main__':
    C = training_frame()
    for Y in [int(a) for a in sys.argv[1:]]:
        m, s, b, n = fit(C, Y)
        print(f'    {Y}: ({float(m)!r}, {float(s)!r}, {float(b)!r}),   # {n:,} stock-days before {Y}')
