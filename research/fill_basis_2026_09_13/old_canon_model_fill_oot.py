"""OOT 2026: old canon (archived pre-09-11 oot payload, 0.80x clamped LAST) repriced at m x smile-fit model credit vs D_ent canon."""
import json, sys, warnings
warnings.filterwarnings('ignore')
import numpy as np, pandas as pd, pyarrow.parquet as pq
sys.path.insert(0, '.')
import ent_canon as ec, report_ent_canon as rec, report_mid_canon as rmc
import subprocess, os
HERE = os.path.dirname(os.path.abspath(__file__))
SCR = HERE + '/'
def _archived(name):
    # the pre-2026-09-11 (old canon) payloads, straight from git; nothing on disk is overwritten
    p = SCR + name
    if not os.path.exists(p):
        open(p, 'w').write(subprocess.check_output(['git', 'show', f"5da76fd~1:live/data/{name.replace('old_', '')}"], text=True))
    return p
P = json.load(open(_archived('old_oot_equity.json')))
T = pd.DataFrame(P['trades']); T['entry_date'] = pd.to_datetime(T.entry)
T = T.rename(columns={'type': 'spread_type', 'k_s': 'short_strike', 'k_l': 'long_strike', 'spot': 'expiry_close',
                      'credit': 'credit80', 'max_loss': 'max_loss80', 'pnl': 'pnl_payload'})
T['width'] = (T.short_strike - T.long_strike).abs().round(4)
print('old canon OOT trades', len(T), T.entry_date.min().date(), T.entry_date.max().date(), 'payload qty2 pnl', T.pnl_payload.sum())
FC = ['ticker', 'entry_date', 'expiry_date', 'DTE', 'entry_price', 'c0', 'c1', 'c2', 'sig0', 'fit_n', 'fit_rmse']
O = pd.read_parquet('research/dkl_2026_09_13/oot_synth.parquet'); O['entry_date'] = pd.to_datetime(O.entry_date); O['expiry_date'] = pd.to_datetime(O.expiry_date)
key = O.drop_duplicates(['ticker', 'entry_date', 'expiry_date'])[FC]
have = set(zip(key.ticker, key.entry_date))
miss = T[[(t, d) not in have for t, d in zip(T.ticker, T.entry_date)]][['ticker', 'entry_date']].drop_duplicates()
print('missing fit keys', len(miss))
if len(miss):
    cols = ['Symbol', 'DataDate', 'ExpirationDate', 'DTE', 'PutCall', 'StrikePrice', 'BidPrice', 'AskPrice', 'ImpliedVolatility', 'UnderlyingPrice']
    d = pq.read_table('output/2026_sp500_last_oot_combined.parquet', columns=cols, filters=[('Symbol', 'in', sorted(miss.ticker.unique())), ('DTE', '<=', 4), ('DTE', '>=', 1)]).to_pandas()
    d['DataDate'] = pd.to_datetime(d.DataDate).dt.normalize(); d['ExpirationDate'] = pd.to_datetime(d.ExpirationDate).dt.normalize()
    d = d.merge(miss.rename(columns={'ticker': 'Symbol', 'entry_date': 'DataDate'}), on=['Symbol', 'DataDate'])
    F = ec.fit_smiles(d)
    sp = d.groupby(['Symbol', 'DataDate', 'ExpirationDate']).agg(entry_price=('UnderlyingPrice', 'median'), DTE=('DTE', 'first')).reset_index()
    F = F.merge(sp, on=['Symbol', 'DataDate', 'ExpirationDate']).rename(columns={'Symbol': 'ticker', 'DataDate': 'entry_date', 'ExpirationDate': 'expiry_date'})
    print('refit chains', len(F), 'keys', F[['ticker', 'entry_date']].drop_duplicates().shape[0])
    key = pd.concat([key, F[FC]]).drop_duplicates(['ticker', 'entry_date', 'expiry_date'])
E0 = O[['ticker', 'expiry_date', 'expiry_close']].dropna().drop_duplicates(['ticker', 'expiry_date']).rename(columns={'expiry_close': 'vendor_close'})
key = key.merge(E0, on=['ticker', 'expiry_date'], how='left')
M = T.merge(key, on=['ticker', 'entry_date'], how='left')
M['_d'] = (M.expiry_close - M.vendor_close).abs()
M = M.sort_values(['ticker', 'entry_date', 'short_strike', '_d', 'DTE']).drop_duplicates(['ticker', 'entry_date', 'spread_type', 'short_strike', 'long_strike'])
print('matched fits', int(M.c0.notna().sum()), 'of', len(M), ' settle-close agrees(<0.01)', int((M._d < 0.01).sum()), ' vendor close missing', int(M.vendor_close.isna().sum()))
M = M[M.c0.notna()].copy()
Pr = ec.price_spreads(M[['ticker', 'entry_date', 'expiry_date', 'DTE', 'spread_type', 'entry_price', 'short_strike', 'long_strike']],
                      M[['ticker', 'entry_date', 'expiry_date', 'c0', 'c1', 'c2', 'sig0', 'fit_n', 'fit_rmse']].drop_duplicates())
M['model_credit'] = Pr.model_credit.values; M['dfit_short'] = Pr.dfit_short.values
M = M[M.model_credit.notna() & (M.model_credit > 0)].copy(); print('priced', len(M))
M['last80'] = M.credit80
print('median 0.80xLAST/model =', (M.credit80 / M.model_credit).median().round(3), ' quantiles', (M.credit80 / M.model_credit).quantile([.1, .25, .5, .75, .9]).round(3).to_dict())
print('median c/w at 0.80xLAST', (M.credit80 / M.width).median().round(3), ' model c/w', (M.model_credit / M.width).median().round(3), ' fitted |short delta| median', M.dfit_short.median().round(3))
print('share booked above 1.08x model:', ((M.credit80 > 1.08 * M.model_credit).mean() * 100).round(1), '%')
M['GROUND'] = M.ground; M['DKL'] = M.dkl; M['G'] = np.log1p(M.kelly_ev / 100.0); M['w_star'] = np.nan
def book(sel, fill, commission=ec.COMMISSION):
    sel = sel.copy(); sel['credit'] = (sel.model_credit * fill).round(4); sel['max_loss_adj'] = (sel.width - sel.credit).round(4)
    sel = sel[sel.max_loss_adj > 0].copy(); sel['_outcome'] = sel.apply(rec._outcome, axis=1)
    keep = ec.COMMISSION; ec.COMMISSION = commission; sel['pnl_per_contract'] = sel.apply(rec._pnl, axis=1); ec.COMMISSION = keep
    sel['max_loss_dollar'] = sel.max_loss_adj * 100; sel['realize_date'] = sel.expiry_date; sel['entry_date_dt'] = sel.entry_date
    return sel.sort_values('entry_date_dt').reset_index(drop=True)
def stats(sel, label):
    s = rmc.build_payload(sel, 2026, label)['summary']; oc = sel._outcome.value_counts(normalize=True)
    return dict(book=label, n=len(sel), first=s['window_start'], last=str(sel.entry_date_dt.max().date()), med_cw=round(float((sel.credit / sel.width).median()), 3),
                win=round(100 * oc.get('WIN', 0), 1), part=round(100 * oc.get('PARTIAL', 0), 1), loss=round(100 * oc.get('LOSS', 0), 1),
                qty1_pnl=round(s['qty1_final'] - 10000), qty1_ret=s['qty1_total_return'], sh_wk=s['qty1_sharpe_weekly'], dd=s['qty1_max_dd'], qty2_pnl=round(s['strategy_final'] - 10000))
rows = []
chk = M.copy(); chk['model_credit'] = chk.last80
b = book(chk, 1.0, commission=0.0); print('REPRO old @0.80xLAST no comm: qty2', round(b.pnl_per_contract.sum() * 2), ' payload matched rows', round(M.pnl_payload.sum()))
rows.append(stats(b, 'OLD @0.80x LAST, no comm (published)')); rows.append(stats(book(chk, 1.0), 'OLD @0.80x LAST + comm'))
for m in (1.00, 1.04, 1.08): rows.append(stats(book(M, m), f'OLD @{m:.2f}x model + comm'))
END = T.entry_date.max()
for m in (1.00, 1.04, 1.08):
    ec.FILL_MULT = m; N = rec.select('OOT')
    rows.append(stats(N, f'NEW @{m:.2f}x model, full to 09-11'))
    rows.append(stats(N[N.entry_date_dt <= END].copy(), f'NEW @{m:.2f}x model, same window'))
R = pd.DataFrame(rows); pd.set_option('display.width', 250); print(R.to_string(index=False)); R.to_csv(SCR + 'old_vs_new_model_fill_oot.csv', index=False)
