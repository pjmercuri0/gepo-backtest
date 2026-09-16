"""Regime-adaptive TA forecasts feeding P_real (user, 2026-09-16).

The per-year IC table shows most TA features flip sign between regimes (momentum pays 2022-23, reversal pays
2020 and 2024-25). An expanding-window fit averages those regimes to ~0. Here each feature's slope is refitted
at the start of every month on only the trailing 6 or 12 months of stock-days, then pushed into P_real as drift.
Stock-level features and market (breadth) features both. Test months 2022-01..2025-12.

    python3 research/ta_direction/ta_predict_roll.py
"""
import sys
sys.argv = ['ta_predict_mkt.py', '--perms', '0']
exec(compile(open('research/ta_direction/ta_predict_mkt.py').read().split('RES = run_mkt(MKV)')[0], 'ta_predict_mkt.py[1-5]', 'exec'))

Xs = np.clip(C[RAW_DIR].values.astype(float), -50, 50)
Xm = np.full((len(C), len(MCOLS)), np.nan); Xm[cpos_ok] = MKV[cpos[cpos_ok]]
XA = np.column_stack([Xs, Xm]); NAMES = RAW_DIR + MCOLS
MON = C.entry_date.dt.to_period('M')
MONTHS = pd.period_range('2022-01', '2025-12', freq='M')
def rolling_z(months_back):
    Z = np.full(XA.shape, np.nan)
    for mth in MONTHS:
        lo = (mth - months_back).start_time; hi = mth.start_time
        tr = TRAIN_OK & (C.entry_date >= lo).values & (C.entry_date < hi).values & np.isfinite(Y)
        te = (MON == mth).values & ISM
        if tr.sum() < 300 or not te.any(): continue
        Xt = XA[tr]; m = np.nanmean(Xt, 0); s = np.nanstd(Xt, 0); s[~(s > 0)] = 1
        Zt = np.clip(np.nan_to_num((Xt - m) / s), -4, 4); yc = Y[tr] - Y[tr].mean()
        b_ = (Zt * yc[:, None]).sum(0) / np.maximum((Zt * Zt).sum(0), 1e-9)
        Z[te] = np.clip(np.nan_to_num((XA[te] - m) / s), -4, 4) * b_
    return Z
rows = []; ROLL = {}
for mb in (6, 12):
    Z = rolling_z(mb); ROLL[mb] = Z
    for j, f in enumerate(NAMES):
        for gm in (1.0, 3.0):
            sel = book_g(ground(np.nan_to_num(gm * Z[:, j]) * SDD * np.sqrt(DSESS)))
            rows.append(dict(model=f, months=mb, gamma=gm, **evaluate(sel)))
    log(f'rolling {mb}m: scored {len(NAMES)*2} variants')
TR = pd.DataFrame(rows)
pd.set_option('display.width', 250); pd.set_option('display.max_rows', 100)
print(f'\n=== rolling-slope TA forecast -> P_real, test 2022-25 (canon {int((BASE & TEST).sum())} trades ${PN[BASE & TEST].sum():,.0f}); {len(TR)} variants ===')
print(TR.sort_values('t', ascending=False).head(30).to_string(index=False))
print('\nshare of variants with dPnL > 0:', round((TR.dPnL > 0).mean(), 3), ' with all 4 years up:', int((TR.yrs_up == 4).sum()))
TR.drop(columns='yrs').to_csv('research/ta_direction/ta_predict_roll_results.csv', index=False)
log('done')
