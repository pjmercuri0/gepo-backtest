"""DELTA_TARGET = 0.20 test on 2020-2025, otherwise IDENTICAL to the canon
builder backtest_midsel_sweep.py (mid-basis selection, G_rv probs, per-date
empirical window, partial-WIN 50% haircut, MIN_OI=100). Single delta config,
NOT a sweep.

Canon uses DELTA_TARGET=0.50 with band [0.35,0.65]. Here the short leg targets
0.20 with band [0.10,0.30] (same bracket shape around the target). Everything
else — k=10, thr=0.05, top-5/day, 0.80x mid fill — is held at canon.

Reports the delta-20 strategy metrics and re-runs the momentum gate-2 gradient
on the delta-20 qualifying picks: the whole point of dropping delta is to see
whether a directional signal becomes first-order once the trade is no longer an
ATM coin-flip.
"""
import sys, os
import numpy as np, pandas as pd
sys.path.insert(0, '.')
import config as bt_config
import spreads, ground
import empirical_runner as er

SP100 = set(bt_config.SP100_TICKERS)
SPY_CSV = 'data/spy_us_d.csv'
START_BANKROLL = 10_000.0
ACTIVE_DOWS = [0, 1, 2, 3]
K_VAL, THR, FILL = 10.0, 0.05, 0.80
CACHE = 'output/sweep_delta20_2020_25.parquet'

# --- the only change from canon: short leg targets 0.20 delta ---
bt_config.DELTA_TARGET = 0.20
bt_config.DELTA_MIN = 0.10
bt_config.DELTA_MAX = 0.30
bt_config.CREDIT_BASIS = "mid"
bt_config.CREDIT_SCALE = 1.0

IV_RANK_LOOKUP = None
try:
    _ivr = pd.read_parquet('output/iv_rank.parquet'); _ivr['DataDate'] = pd.to_datetime(_ivr['DataDate'])
    IV_RANK_LOOKUP = _ivr[['Symbol', 'DataDate', 'iv_rank_bucket']]
except FileNotFoundError:
    print('WARNING: iv_rank.parquet missing')
RV_LOOKUP = None
try:
    _rv = pd.read_parquet('output/rv_table.parquet'); _rv['DataDate'] = pd.to_datetime(_rv['DataDate'])
    RV_LOOKUP = _rv[['Symbol', 'DataDate', 'rv_30d']]
except FileNotFoundError:
    print('WARNING: rv_table.parquet missing')

POOL = None


def score_and_realize_year(year, min_entry_date):
    df_full = pd.read_parquet(f'output/{year}_sp500_last.parquet')
    df_full = df_full[df_full['Symbol'].isin(SP100)]
    expiry_close = (df_full[df_full['DataDate'] == df_full['ExpirationDate']]
                    .groupby(['Symbol', 'ExpirationDate'])['UnderlyingPrice'].first().to_dict())
    df = df_full.copy()
    df['dow'] = df['DataDate'].dt.dayofweek
    df['exp_dow'] = df['ExpirationDate'].dt.dayofweek
    df = df[df['dow'].isin(ACTIVE_DOWS)]
    df = df[df['exp_dow'] == 4]
    df = df[(df['DTE'] >= 1) & (df['DTE'] <= 4)]
    df = df[df['LastPrice'].astype(float) > 0]
    df = df[df['DataDate'] >= min_entry_date]
    _spy = pd.read_csv(SPY_CSV, parse_dates=['Date'])
    df = df[df['DataDate'].isin(set(_spy['Date']))]
    df = df.copy()
    df['AbsDelta'] = df['Delta'].abs()
    df['MidPrice'] = (df['BidPrice'] + df['AskPrice']) / 2.0
    if IV_RANK_LOOKUP is not None:
        df = df.merge(IV_RANK_LOOKUP, on=['Symbol', 'DataDate'], how='left')
    if RV_LOOKUP is not None:
        df = df.merge(RV_LOOKUP, on=['Symbol', 'DataDate'], how='left')

    spreads.REGIME_LOOKUP = spreads.build_regime_lookup(SPY_CSV, sma_window=100)
    spreads.REGIME_FILTER = False
    spreads.REGIME_PER_TICKER = False
    spreads.GAP_FILTER = False
    spreads.LOW_VIX_BULLPUT_FILTER = False
    spreads.SLIPPAGE_CENTS = 0.0
    bt_config.MIN_OPEN_INTEREST = 100

    candidates = spreads.build_candidates(df)
    if candidates.empty:
        return pd.DataFrame()

    parts = []
    for dt in sorted(candidates['entry_date'].unique()):
        sub = candidates[candidates['entry_date'] == dt]
        if sub.empty: continue
        ok = er.install_window(POOL, pd.Timestamp(dt))
        if not ok:
            import historical_probs as hp
            hp._EMPIRICAL_TABLE = None
        try:
            parts.append(ground.score_candidates(sub))
        except Exception as e:
            print(f'  {dt}: score failed ({e}); uniform fallback')
            import historical_probs as hp
            hp._EMPIRICAL_TABLE = None
            parts.append(ground.score_candidates(sub))
    scored = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
    scored = scored.dropna(subset=['G', 'DKL']).copy()

    scored['expiry_close'] = scored.apply(lambda r: expiry_close.get((r['ticker'], r['expiry_date'])), axis=1)
    ok = scored.dropna(subset=['expiry_close']).copy()
    ok['width'] = ok['net_credit'] + ok['max_loss']

    def _oc(r):
        sp, ss, ls = r['expiry_close'], r['short_strike'], r['long_strike']
        if r['spread_type'] == 'bull_put':
            return 'WIN' if sp > ss else ('LOSS' if sp <= ls else 'PARTIAL')
        return 'WIN' if sp < ss else ('LOSS' if sp >= ls else 'PARTIAL')
    ok['_outcome'] = ok.apply(_oc, axis=1)

    credit = ok['net_credit'] * FILL
    ml = ok['width'] - credit
    pnl = ok.apply(lambda r, c=credit, m=ml: spreads.calc_pnl(
        r['expiry_close'], r['short_strike'], r['long_strike'],
        c.loc[r.name], m.loc[r.name], r['spread_type']), axis=1) * 100
    mask = (ok['_outcome'] == 'PARTIAL') & (pnl > 0)
    pnl[mask] *= 0.5
    ok['pnl_80'] = pnl

    keep = ['entry_date', 'expiry_date', 'ticker', 'spread_type', 'short_strike', 'long_strike',
            'net_credit', 'max_loss', 'width', 'G', 'DKL', 'expiry_close', '_outcome', 'w_star', 'pnl_80']
    return ok[[c for c in keep if c in ok.columns]]


def build_cache():
    if os.path.exists(CACHE):
        print(f'Loading delta-20 cache {CACHE}')
        return pd.read_parquet(CACHE)
    global POOL
    POOL = er.load_master_pool()
    print(f'Loaded master pool: {len(POOL):,} rows', flush=True)
    parts = []
    for y in [2020, 2021, 2022, 2023, 2024, 2025]:
        print(f'-- {y} --', flush=True)
        r = score_and_realize_year(y, pd.Timestamp('2020-01-01'))
        print(f'   scored+realized: {len(r):,}', flush=True)
        parts.append(r)
    c = pd.concat([p for p in parts if not p.empty], ignore_index=True)
    c.to_parquet(CACHE)
    print(f'Wrote {CACHE}: {len(c):,} rows')
    return c


def evaluate(R):
    R = R.copy()
    R['entry_date'] = pd.to_datetime(R['entry_date'])
    spy = pd.read_csv(SPY_CSV, parse_dates=['Date']); td_all = set(spy['Date'].dt.normalize())
    R = R[R['entry_date'].dt.normalize().isin(td_all)]
    R['GROUND'] = (np.exp(R['G']) - 1.0) * np.exp(-K_VAL * R['DKL'])
    sel = (R[R['GROUND'] >= THR].sort_values(['entry_date', 'GROUND'], ascending=[True, False])
           .groupby('entry_date').head(5)).copy()
    spy = spy.sort_values('Date')
    spy = spy[(spy['Date'] >= sel['entry_date'].min()) & (spy['Date'] <= pd.Timestamp('2025-12-31'))]
    td = pd.DatetimeIndex(spy['Date'])
    daily = sel.groupby(pd.to_datetime(sel['expiry_date']))['pnl_80'].sum()
    eq = START_BANKROLL + daily.reindex(td, fill_value=0.0).cumsum()
    weekly = eq.resample('W-FRI').last().ffill().pct_change().dropna()
    wsd = weekly.std(ddof=0)
    wsh = float(weekly.mean() * np.sqrt(52) / wsd) if wsd > 0 else 0.0
    peak = eq.cummax(); dd = 100 * float(((eq - peak) / peak).min())
    final = float(eq.iloc[-1])
    n_years = (td[-1] - td[0]).days / 365.25
    cagr = 100 * ((final / START_BANKROLL) ** (1 / n_years) - 1) if final > 0 else -100
    return sel, dict(n=len(sel), final=final, cagr=cagr, sh_wk=wsh, dd=dd,
                     win=100 * (sel['pnl_80'] > 0).mean(), per_tr=sel['pnl_80'].mean(),
                     credit=sel['net_credit'].mean(), max_loss=sel['max_loss'].mean(),
                     short_delta=None)


def momentum_gate(sel):
    m = pd.read_parquet('output/momentum_gate_candidates.parquet',
                        columns=['ticker', 'entry_date', 'mom_20']).drop_duplicates(['ticker', 'entry_date'])
    m['entry_date'] = pd.to_datetime(m['entry_date'])
    sel = sel.merge(m, on=['ticker', 'entry_date'], how='left')
    sel['breach'] = (sel['_outcome'] != 'WIN')
    print(f'\n{"="*64}\nMomentum gate-2 on DELTA-20 qualifying picks (coverage {100*sel.mom_20.notna().mean():.0f}%)\n{"="*64}')
    for st in ['bull_put', 'bear_call']:
        sub = sel[sel.spread_type == st].dropna(subset=['mom_20']).copy()
        print(f'\n  {st}  (n={len(sub):,})')
        if len(sub) < 100:
            print('    too small'); continue
        try:
            sub['q'] = pd.qcut(sub['mom_20'], 5, labels=[1,2,3,4,5], duplicates='drop')
        except ValueError:
            print('    cannot quintile'); continue
        g = sub.groupby('q', observed=True)
        pnl, br, n = g['pnl_80'].mean(), g['breach'].mean()*100, g.size()
        print(f'    {"Q":>2} {"n":>5} {"P&L/ctr":>9} {"breach%":>8}')
        for q in sorted(sub['q'].dropna().unique()):
            print(f'    {int(q):>2} {int(n[q]):>5} {pnl[q]:>9.2f} {br[q]:>8.1f}')
        if 5 in pnl.index and 1 in pnl.index:
            a = sub[sub['q']==5]['pnl_80']; b = sub[sub['q']==1]['pnl_80']
            se = np.sqrt(a.var(ddof=1)/len(a)+b.var(ddof=1)/len(b))
            t = (a.mean()-b.mean())/se if se>0 else float('nan')
            print(f'    Q5-Q1 P&L: {a.mean()-b.mean():+.2f}  t={t:+.2f}')


if __name__ == '__main__':
    R = build_cache()
    sel, m = evaluate(R)
    print(f'\n{"="*64}\nDELTA-20 STRATEGY (k=10, thr=0.05, top-5/day, 0.80x mid) 2020-2025\n{"="*64}')
    print(f'  n trades:        {m["n"]:,}')
    print(f'  win rate:        {m["win"]:.1f}%')
    print(f'  P&L / contract:  ${m["per_tr"]:.2f}')
    print(f'  mean credit:     ${m["credit"]:.3f}   mean max_loss: ${m["max_loss"]:.3f}')
    print(f'  final (qty1):    ${m["final"]:,.0f}')
    print(f'  CAGR:            {m["cagr"]:.1f}%')
    print(f'  weekly Sharpe:   {m["sh_wk"]:+.2f}')
    print(f'  max drawdown:    {m["dd"]:.1f}%')
    print(f'\n  CANON delta-50 reference (qty1): final $50,799  CAGR 31.2%  Sh(wk) 2.44  DD -5.8%  win ~? n=2250')
    momentum_gate(sel)
