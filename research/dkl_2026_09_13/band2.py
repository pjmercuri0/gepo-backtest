import sys, os, glob, numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import dkl_eval as de
import band_sweep as bsw
from band_checks import build, book_comm
pd.set_option('display.width', 250)
BANDS = [(0.15, 0.25, 0.20), (0.20, 0.30, 0.25), (0.25, 0.35, 0.30), (0.30, 0.40, 0.35), (0.35, 0.45, 0.40), (0.40, 0.50, 0.45), (0.45, 0.55, 0.50), (0.50, 0.60, 0.55),
         (0.20, 0.40, 0.30), (0.30, 0.50, 0.40), (0.40, 0.60, 0.50), (0.20, 0.60, 0.40)]
rows = []
for lo, hi, tgt in BANDS:
    ISc = build(lo, hi, tgt, bsw.IS_ALL); OOc = build(lo, hi, tgt, bsw.OO_ALL)
    for fill in (1.1, 1.0):
        de.FILL, de.THR = fill, 0.01
        for k in (0, 8):
            a = book_comm(bsw.select_reward(ISc, k), pd.Timestamp('2025-12-31'), 1.30); o = book_comm(bsw.select_reward(OOc, k), pd.Timestamp('2026-08-31'), 1.30)
            rows.append(dict(band=f'{lo:.2f}-{hi:.2f}', tgt=tgt, fill=fill, k=k, cands=len(ISc), cred_w=round(float(ISc.eval('net_credit/width').median()), 3), raw_win=round(float((ISc.outcome == 'WIN').mean()), 3),
                             is_n=a['n'], is_fin=round(a['final']), is_sh=round(a['sharpe'], 2), is_dd=round(a['dd'], 1), worst_wk=round(a['worst_wk'], 1), oot_n=o['n'], oot_fin=round(o['final']), oot_sh=round(o['sharpe'], 2), oot_dd=round(o['dd'], 1)))
    print(f'band {lo:.2f}-{hi:.2f} done', flush=True)
R = pd.DataFrame(rows); R.to_csv(f'{HERE}/band2.csv', index=False)
for fill in (1.1, 1.0):
    for k in (0, 8):
        print(f'\n===== 20-60 delta, fill {fill}x model, k={k}, thr 0.01, commission $1.30/spread =====')
        print(R[(R.fill == fill) & (R.k == k)].drop(columns=['fill', 'k']).to_string(index=False))
# fillability on IBKR chains at 40-60 delta
import synth_credit as sc
rows = []
for f in sorted(glob.glob('live/snapshots/2026-*/15*.parquet'))[:60]:
    d = pd.read_parquet(f); d['PutCall'] = d.PutCall.str.lower().str.strip(); d = d[d.DTE.between(1, 4) & (d.UnderlyingPrice > 0)].copy()
    if d.empty: continue
    d['mid'] = (d.BidPrice + d.AskPrice) / 2; d = d.sort_values(['Symbol', 'DataDate', 'ExpirationDate', 'PutCall', 'StrikePrice']); gb = d.groupby(['Symbol', 'DataDate', 'ExpirationDate', 'PutCall'], sort=False)
    for c in ['StrikePrice', 'mid', 'BidPrice', 'AskPrice']: d['lo_' + c] = gb[c].shift(1); d['hi_' + c] = gb[c].shift(-1)
    put = d[d.PutCall == 'put'].copy(); put['spread_type'] = 'bull_put'
    for c in ['StrikePrice', 'mid', 'BidPrice', 'AskPrice']: put['L_' + c] = put['lo_' + c]
    cal = d[d.PutCall == 'call'].copy(); cal['spread_type'] = 'bear_call'
    for c in ['StrikePrice', 'mid', 'BidPrice', 'AskPrice']: cal['L_' + c] = cal['hi_' + c]
    x = pd.concat([put, cal]).dropna(subset=['L_StrikePrice']); x['width'] = (x.StrikePrice - x.L_StrikePrice).abs(); x = x[(x.width > 0) & (x.width <= 2.5)]
    x = x.rename(columns={'Symbol': 'ticker', 'DataDate': 'entry_date', 'ExpirationDate': 'expiry_date', 'StrikePrice': 'short_strike', 'L_StrikePrice': 'long_strike', 'UnderlyingPrice': 'entry_price'})
    x['net_credit'] = x.mid - x.L_mid; x['natural'] = x.BidPrice - x.L_AskPrice
    try: R2 = sc.synth(x[['ticker', 'entry_date', 'expiry_date', 'DTE', 'spread_type', 'entry_price', 'short_strike', 'long_strike', 'net_credit', 'natural', 'width', 'BidPrice', 'AskPrice']], [f])
    except Exception: continue
    T = np.clip(R2.DTE.values, 1, None) / 365; S = R2.entry_price.values; K = R2.short_strike.values; iv = R2.iv_fit_short.values
    d1 = (np.log(S / K) + 0.5 * iv * iv * T) / (iv * np.sqrt(T)); R2['dfit'] = np.where(R2.spread_type == 'bull_put', sc.ncdf(-d1), sc.ncdf(d1)); rows.append(R2)
L = pd.concat(rows, ignore_index=True).dropna(subset=['model_credit']); L = L[L.model_credit > 0]
print('\n===== FILLABILITY on real IBKR 15:31 chains, by fitted delta band: model credit vs IBKR mid / natural =====')
for lo, hi in [(0.15, 0.25), (0.25, 0.35), (0.35, 0.45), (0.45, 0.55), (0.40, 0.60)]:
    s = L[L.dfit.between(lo, hi)]
    print(f'  {lo:.2f}-{hi:.2f}: n={len(s):>5}  model med ${s.model_credit.median():.3f}  IBKR mid ${s.net_credit.median():.3f}  natural ${s.natural.median():.3f}  model/mid med {(s.model_credit/s.net_credit.clip(lower=0.005)).median():.2f}  '
          f'natural>=model {(s.natural >= s.model_credit).mean():.1%}  natural>=0.8model {(s.natural >= 0.8*s.model_credit).mean():.1%}  short bid-ask ${(s.AskPrice-s.BidPrice).median():.2f}')
