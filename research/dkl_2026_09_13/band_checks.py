import sys, os, glob, time, numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import dkl_eval as de
from dkl_eval import book, pnl_of, outcome_of
import band_sweep as bsw   # reuses load_pairs / p_real / select_reward (module-level loads run again, ~10s)
from diag import iv_triple
from dkl_eval import kl, kelly_ell
pd.set_option('display.width', 250)
def build(lo, hi, tgt, A):
    b = A[A.dfit_short.between(lo, hi) & (A.dfit_long < A.dfit_short) & (A.model_credit > 0.01)].copy(); b['dist'] = (b.dfit_short - tgt).abs()
    C = b.loc[b.groupby(['ticker', 'entry_date', 'expiry_date', 'spread_type'], sort=False)['dist'].idxmin()].copy()
    C['net_credit'] = C.model_credit; C['max_loss'] = (C.width - C.model_credit).round(4); C = C[C.max_loss > 0]
    P = bsw.p_real(C); C = C.reset_index(drop=True); C['p'], C['q'], C['ro'] = P[:, 0], P[:, 1], P[:, 2]
    ell = kelly_ell(C.p.fillna(0).values, C.q.fillna(0).values, C.ro.fillna(0).values, C.net_credit.values / C.max_loss.values); C['EV'] = np.exp(ell) - 1; C.loc[C.p.isna(), 'EV'] = np.nan
    Qw = iv_triple(C, C.iv_fit_short.values, C.iv_fit_long.values); Qa = iv_triple(C, C.sig0.values, C.sig0.values)
    C['D'] = np.where(Qw[:, 1] + 0.5 * Qw[:, 2] > Qa[:, 1] + 0.5 * Qa[:, 2], kl(Qw, Qa), 0.0); C['outcome'] = outcome_of(C)
    return C.dropna(subset=['EV'])
def book_comm(sel, end, comm):
    s = sel.copy(); s['credit'] = (s.net_credit * de.FILL).round(4); s['ml'] = (s.width - s.credit).round(4); s = s[s.ml > 0].copy()
    s['pnl'] = pnl_of(s, s.credit.values, s.ml.values) - comm
    if len(s) == 0: return dict(n=0, final=10000.0, sharpe=0.0, dd=0.0, worst_wk=0.0)
    cal = pd.bdate_range(s.entry_date.min(), end); eq = 10000.0 + s.groupby('expiry_date')['pnl'].sum().reindex(cal, fill_value=0).cumsum()
    w = eq.resample('W-FRI').last().ffill().pct_change().dropna(); sd = w.std(ddof=0)
    return dict(n=len(s), final=float(eq.iloc[-1]), sharpe=float(w.mean() * np.sqrt(52) / sd) if sd > 0 else 0.0, dd=100 * float(((eq - eq.cummax()) / eq.cummax()).min()), worst_wk=100 * float(w.min()))
print('===== A. COMMISSION STRESS (per spread per contract, opening only; IBKR ~$0.65/leg) — fill 1.1, k=8, thr 0.01 =====')
de.THR = 0.01
frames = {}
for lo, hi, tgt in [(0.05, 0.15, 0.10), (0.10, 0.20, 0.15), (0.15, 0.25, 0.20), (0.40, 0.60, 0.50)]:
    ISc = build(lo, hi, tgt, bsw.IS_ALL); OOc = build(lo, hi, tgt, bsw.OO_ALL); frames[tgt] = (ISc, OOc)
    for fill in (1.1, 1.0):
        de.FILL = fill
        for comm in (0.0, 1.30, 2.60):
            a = book_comm(bsw.select_reward(ISc, 8), pd.Timestamp('2025-12-31'), comm); o = book_comm(bsw.select_reward(OOc, 8), pd.Timestamp('2026-08-31'), comm)
            print(f'  tgt {tgt:.2f} fill {fill} comm ${comm:.2f}: IS n={a["n"]} ${a["final"]:>8,.0f} Sh {a["sharpe"]:5.2f} DD {a["dd"]:6.1f}% worst wk {a["worst_wk"]:5.1f}% | OOT ${o["final"]:>7,.0f} Sh {o["sharpe"]:5.2f} DD {o["dd"]:6.1f}%')
print('\n===== C. 10-delta band, fill 1.1, k=8: year by year and worst weeks =====')
ISc, OOc = frames[0.10]; de.FILL = 1.1
C = pd.concat([ISc, OOc]); C['entry_date'] = pd.to_datetime(C.entry_date)
for y in range(2020, 2027):
    sub = C[C.entry_date.dt.year == y]; r = book_comm(bsw.select_reward(sub, 8), pd.Timestamp(f'{y}-12-31') if y < 2026 else pd.Timestamp('2026-08-31'), 1.30)
    print(f'  {y}: n={r["n"]:>4} final ${r["final"]:>7,.0f} Sh {r["sharpe"]:5.2f} DD {r["dd"]:6.1f}% worst week {r["worst_wk"]:5.1f}%   (comm $1.30)')
s = bsw.select_reward(ISc, 8); s['credit'] = (s.net_credit * 1.1).round(4); s['ml'] = s.width - s.credit; s['pnl'] = pnl_of(s, s.credit.values, s.ml.values) - 1.30
wk = s.groupby(pd.to_datetime(s.expiry_date)).pnl.sum().sort_values()
print('  five worst expiry weeks (IS):', [(d.date().isoformat(), round(v)) for d, v in wk.head(5).items()])
print(f'  10-delta book: median credit ${s.credit.median()*100:.0f}/contract, median max loss ${(s.ml.median()*100):.0f}, share credit < $0.05/share {(s.net_credit < 0.05).mean():.1%}, median OTM {100*np.where(s.spread_type=="bull_put", 1-s.short_strike/s.entry_price, s.short_strike/s.entry_price-1).mean():.2f}%')
print('\n===== B. FILLABILITY at 10-delta on REAL IBKR chains (15:31 snapshots): smile-fit model credit vs IBKR mid / natural =====')
import synth_credit as sc
rows = []
for f in sorted(glob.glob('live/snapshots/2026-*/15*.parquet'))[:60]:
    d = pd.read_parquet(f); d['PutCall'] = d.PutCall.str.lower().str.strip()
    d = d[d.DTE.between(1, 4) & (d.UnderlyingPrice > 0)].copy()
    if d.empty: continue
    d['mid'] = (d.BidPrice + d.AskPrice) / 2; d = d.sort_values(['Symbol', 'DataDate', 'ExpirationDate', 'PutCall', 'StrikePrice'])
    gb = d.groupby(['Symbol', 'DataDate', 'ExpirationDate', 'PutCall'], sort=False)
    for c in ['StrikePrice', 'mid', 'BidPrice', 'AskPrice']:
        d['lo_' + c] = gb[c].shift(1); d['hi_' + c] = gb[c].shift(-1)
    put = d[d.PutCall == 'put'].copy(); put['spread_type'] = 'bull_put'
    for c in ['StrikePrice', 'mid', 'BidPrice', 'AskPrice']: put['L_' + c] = put['lo_' + c]
    cal = d[d.PutCall == 'call'].copy(); cal['spread_type'] = 'bear_call'
    for c in ['StrikePrice', 'mid', 'BidPrice', 'AskPrice']: cal['L_' + c] = cal['hi_' + c]
    x = pd.concat([put, cal]).dropna(subset=['L_StrikePrice'])
    x['width'] = (x.StrikePrice - x.L_StrikePrice).abs(); x = x[(x.width > 0) & (x.width <= 2.5)]
    x = x.rename(columns={'Symbol': 'ticker', 'DataDate': 'entry_date', 'ExpirationDate': 'expiry_date', 'StrikePrice': 'short_strike', 'L_StrikePrice': 'long_strike', 'UnderlyingPrice': 'entry_price'})
    x['net_credit'] = x.mid - x.L_mid; x['natural'] = x.BidPrice - x.L_AskPrice
    try:
        R = sc.synth(x[['ticker', 'entry_date', 'expiry_date', 'DTE', 'spread_type', 'entry_price', 'short_strike', 'long_strike', 'net_credit', 'natural', 'width', 'BidPrice', 'AskPrice', 'L_BidPrice', 'L_AskPrice']], [f])
    except Exception as e:
        continue
    T = np.clip(R.DTE.values, 1, None) / 365; S = R.entry_price.values; K = R.short_strike.values; iv = R.iv_fit_short.values
    d1 = (np.log(S / K) + 0.5 * iv * iv * T) / (iv * np.sqrt(T)); R['dfit'] = np.where(R.spread_type == 'bull_put', sc.ncdf(-d1), sc.ncdf(d1))
    rows.append(R[R.dfit.between(0.05, 0.15)])
L = pd.concat(rows, ignore_index=True).dropna(subset=['model_credit']); L = L[L.model_credit > 0]
print(f'  10-delta spreads on IBKR chains: {len(L):,} from {len(rows)} snapshots')
print(f'  model credit median ${L.model_credit.median():.3f}; IBKR mid median ${L.net_credit.median():.3f}; IBKR natural median ${L.natural.median():.3f}')
print(f'  model / IBKR mid: {(L.model_credit / L.net_credit.clip(lower=0.005)).quantile([.1,.25,.5,.75,.9]).round(2).tolist()}')
print(f'  share where IBKR natural >= model credit (fillable at model without improvement): {(L.natural >= L.model_credit).mean():.1%}')
print(f'  share where IBKR natural >= 0.8 x model: {(L.natural >= 0.8*L.model_credit).mean():.1%};  share short-leg bid <= $0.01: {(L.BidPrice <= 0.01).mean():.1%};  share model credit < $0.05: {(L.model_credit < 0.05).mean():.1%}')
print(f'  short-leg bid-ask width median ${(L.AskPrice-L.BidPrice).median():.2f} vs model credit ${L.model_credit.median():.3f}')
