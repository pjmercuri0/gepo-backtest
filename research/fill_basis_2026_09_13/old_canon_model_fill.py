"""Old canon (pre-2026-09-11: delta 0.50 band 0.35-0.65, G_rv, rv_vs_iv k=10, thr 0.05, top-5/day)
trade set from the archived payload, repriced at m x smile-fit model credit, vs the D_ent canon at the same m."""
import json, sys, warnings
warnings.filterwarnings('ignore')
import numpy as np, pandas as pd
sys.path.insert(0, '.')
import ent_canon as ec
import report_ent_canon as rec
import report_mid_canon as rmc

import subprocess, os
HERE = os.path.dirname(os.path.abspath(__file__))
SCR = HERE + '/'
def _archived(name):
    # the pre-2026-09-11 (old canon) payloads, straight from git; nothing on disk is overwritten
    p = SCR + name
    if not os.path.exists(p):
        open(p, 'w').write(subprocess.check_output(['git', 'show', f"5da76fd~1:live/data/{name.replace('old_', '')}"], text=True))
    return p
P = json.load(open(_archived('old_backtest_equity.json')))
T = pd.DataFrame(P['trades'])
T['entry_date'] = pd.to_datetime(T.entry)
T = T.rename(columns={'type': 'spread_type', 'k_s': 'short_strike', 'k_l': 'long_strike', 'spot': 'expiry_close',
                      'credit': 'credit80', 'max_loss': 'max_loss80', 'pnl': 'pnl_payload'})
T['width'] = (T.short_strike - T.long_strike).abs().round(4)
print('old canon trades', len(T), 'payload qty2 pnl sum', T.pnl_payload.sum())

S = pd.read_parquet('research/dkl_2026_09_13/is_synth.parquet')
S['entry_date'] = pd.to_datetime(S.entry_date); S['expiry_date'] = pd.to_datetime(S.expiry_date)
FC = ['ticker', 'entry_date', 'expiry_date', 'DTE', 'entry_price', 'c0', 'c1', 'c2', 'sig0', 'fit_n', 'fit_rmse']
key = S.drop_duplicates(['ticker', 'entry_date', 'expiry_date'])[FC]
RF = pd.read_parquet(SCR + 'refit_missing.parquet')[FC]
key = pd.concat([key, RF]).drop_duplicates(['ticker', 'entry_date', 'expiry_date'])
E0 = pd.read_parquet('/tmp/gepo_expclose.parquet'); E0['expiry_date'] = pd.to_datetime(E0.expiry_date)
key = key.merge(E0.rename(columns={'expiry_close': 'vendor_close'}), on=['ticker', 'expiry_date'], how='left')

M = T.merge(key, on=['ticker', 'entry_date'], how='left')
M['_d'] = (M.expiry_close - M.vendor_close).abs()
M = M.sort_values(['ticker', 'entry_date', 'short_strike', '_d', 'DTE']).drop_duplicates(['ticker', 'entry_date', 'spread_type', 'short_strike', 'long_strike'])
print('matched fits', M.c0.notna().sum(), 'of', len(M), ' settle-close agrees(<0.01)', int((M._d < 0.01).sum()), ' vendor close missing', int(M.vendor_close.isna().sum()))
print('unmatched by year', M[M.c0.isna()].entry_date.dt.year.value_counts().sort_index().to_dict())
M = M[M.c0.notna()].copy()
fits = M[['ticker', 'entry_date', 'expiry_date', 'c0', 'c1', 'c2', 'sig0', 'fit_n', 'fit_rmse']].drop_duplicates()
cands = M[['ticker', 'entry_date', 'expiry_date', 'DTE', 'spread_type', 'entry_price', 'short_strike', 'long_strike']]
Pr = ec.price_spreads(cands, fits)
M['model_credit'] = Pr.model_credit.values; M['dfit_short'] = Pr.dfit_short.values; M['D_ent'] = Pr.D_ent.values
M = M[M.model_credit.notna() & (M.model_credit > 0)].copy()
print('priced', len(M))
M['mid'] = M.credit80 / 0.80
print('median 0.80xmid/model =', (M.credit80 / M.model_credit).median().round(3), ' mid/model =', (M.mid / M.model_credit).median().round(3))
print('median c/w at 0.80xmid', (M.credit80 / M.width).median().round(3), ' model c/w', (M.model_credit / M.width).median().round(3), ' fitted |short delta| median', M.dfit_short.median().round(3))
print('share of trades where 0.80xmid > 1.08x model:', ((M.credit80 > 1.08 * M.model_credit).mean() * 100).round(1), '%')
M['GROUND'] = M.ground; M['DKL'] = M.dkl; M['G'] = np.log1p(M.kelly_ev / 100.0); M['w_star'] = np.nan


def book(sel, fill, commission=ec.COMMISSION):
    sel = sel.copy()
    sel['credit'] = (sel.model_credit * fill).round(4)
    sel['max_loss_adj'] = (sel.width - sel.credit).round(4)
    sel = sel[sel.max_loss_adj > 0].copy()
    sel['_outcome'] = sel.apply(rec._outcome, axis=1)
    keep = ec.COMMISSION; ec.COMMISSION = commission
    sel['pnl_per_contract'] = sel.apply(rec._pnl, axis=1)
    ec.COMMISSION = keep
    sel['max_loss_dollar'] = sel.max_loss_adj * 100
    sel['realize_date'] = sel.expiry_date; sel['entry_date_dt'] = sel.entry_date
    return sel.sort_values('entry_date_dt').reset_index(drop=True)


def stats(sel, label):
    s = rmc.build_payload(sel, 2025, label)['summary']
    oc = sel._outcome.value_counts(normalize=True)
    return dict(book=label, n=len(sel), med_cw=round(float((sel.credit / sel.width).median()), 3),
                win=round(100 * oc.get('WIN', 0), 1), part=round(100 * oc.get('PARTIAL', 0), 1), loss=round(100 * oc.get('LOSS', 0), 1),
                qty1_pnl=round(s['qty1_final'] - 10000), qty1_ret=s['qty1_total_return'], sh_wk=s['qty1_sharpe_weekly'], dd=s['qty1_max_dd'],
                qty2_pnl=round(s['strategy_final'] - 10000), qty2_dd=s['strategy_max_dd'], start=s['window_start'])

rows = []
chk = M.copy(); chk['model_credit'] = chk.mid
b = book(chk, 0.80, commission=0.0)
print('REPRO old @0.80xmid no comm: qty2 pnl', round(b.pnl_per_contract.sum() * 2), ' payload on matched rows', round(M.pnl_payload.sum()))
rows.append(stats(b, 'OLD canon @0.80x mid, no comm (published basis)'))
rows.append(stats(book(chk, 0.80), 'OLD canon @0.80x mid + $1.30 comm'))
for m in (1.00, 1.04, 1.08):
    rows.append(stats(book(M, m), f'OLD canon @{m:.2f}x model + comm'))
for m in (1.00, 1.04, 1.08):
    ec.FILL_MULT = m
    rows.append(stats(rec.select('IS'), f'NEW D_ent canon @{m:.2f}x model + comm'))
R = pd.DataFrame(rows)
pd.set_option('display.width', 250)
print(R.to_string(index=False))
R.to_csv(SCR + 'old_vs_new_model_fill.csv', index=False)
M.to_parquet(SCR + 'old_canon_priced.parquet')
