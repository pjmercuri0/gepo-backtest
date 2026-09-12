"""Sweep MIN_CREDIT_RATIO at delta-20, full GROUND pipeline, 2020-2025.

Build ONCE with the gate at 0.10 (so the cache holds every candidate down to
0.10), score all through GROUND, realize at 0.80x mid with the partial-WIN
haircut. Then evaluate each gate g in {0.10,0.15,0.20,0.25,0.30} by filtering
credit_ratio >= g BEFORE GROUND selection — identical to applying the gate at
candidate construction, since the ratio gate is a pure per-candidate filter.

Everything else is canon: delta 0.20 (band 0.10-0.30), k=10, thr=0.05,
top-5/day, mid-basis selection, 0.80x fill.
"""
import sys, os
import numpy as np, pandas as pd
sys.path.insert(0, '.')
import config as bt_config
import spreads, ground
import empirical_runner as er

SP100 = set(bt_config.SP100_TICKERS)
SPY_CSV = 'data/spy_us_d.csv'
START = 10_000.0
ACTIVE = [0, 1, 2, 3]
K_VAL, THR, FILL = 10.0, 0.05, 0.80
BUILD_GATE = 0.10
GATES = [0.10, 0.15, 0.20, 0.25, 0.30]
CACHE = 'output/sweep_delta20_mincredit_2020_25.parquet'

bt_config.DELTA_TARGET, bt_config.DELTA_MIN, bt_config.DELTA_MAX = 0.20, 0.10, 0.30
bt_config.CREDIT_BASIS = "mid"; bt_config.CREDIT_SCALE = 1.0

IV_RANK_LOOKUP = RV_LOOKUP = None
try:
    _ivr = pd.read_parquet('output/iv_rank.parquet'); _ivr['DataDate'] = pd.to_datetime(_ivr['DataDate'])
    IV_RANK_LOOKUP = _ivr[['Symbol', 'DataDate', 'iv_rank_bucket']]
except FileNotFoundError: pass
try:
    _rv = pd.read_parquet('output/rv_table.parquet'); _rv['DataDate'] = pd.to_datetime(_rv['DataDate'])
    RV_LOOKUP = _rv[['Symbol', 'DataDate', 'rv_30d']]
except FileNotFoundError: pass


def score_year(year, pool):
    df = pd.read_parquet(f'output/{year}_sp500_last.parquet')
    df = df[df['Symbol'].isin(SP100)]
    df['PutCall'] = df['PutCall'].str.lower().str.strip()
    expiry_close = (df[df['DataDate'] == df['ExpirationDate']]
                    .groupby(['Symbol', 'ExpirationDate'])['UnderlyingPrice'].first().to_dict())
    df['dow'] = df['DataDate'].dt.dayofweek; df['exp_dow'] = df['ExpirationDate'].dt.dayofweek
    df = df[df['dow'].isin(ACTIVE) & (df['exp_dow'] == 4) & df['DTE'].between(1, 4)]
    df = df[df['LastPrice'].astype(float) > 0]
    _spy = pd.read_csv(SPY_CSV, parse_dates=['Date'])
    df = df[df['DataDate'].isin(set(_spy['Date']))].copy()
    df['AbsDelta'] = df['Delta'].abs(); df['MidPrice'] = (df['BidPrice'] + df['AskPrice']) / 2.0
    if IV_RANK_LOOKUP is not None: df = df.merge(IV_RANK_LOOKUP, on=['Symbol', 'DataDate'], how='left')
    if RV_LOOKUP is not None: df = df.merge(RV_LOOKUP, on=['Symbol', 'DataDate'], how='left')
    spreads.REGIME_LOOKUP = spreads.build_regime_lookup(SPY_CSV, sma_window=100)
    spreads.REGIME_FILTER = spreads.GAP_FILTER = spreads.LOW_VIX_BULLPUT_FILTER = False
    spreads.EARNINGS_FILTER = spreads.HOLIDAY_FILTER = spreads.REGIME_PER_TICKER = False
    spreads.SLIPPAGE_CENTS = 0.0
    bt_config.MIN_OPEN_INTEREST = 100
    bt_config.MIN_CREDIT_RATIO = BUILD_GATE   # <-- build with the low gate

    cand = spreads.build_candidates(df)
    if cand.empty: return pd.DataFrame()
    parts = []
    for dt in sorted(cand['entry_date'].unique()):
        sub = cand[cand['entry_date'] == dt]
        if sub.empty: continue
        if not er.install_window(pool, pd.Timestamp(dt)):
            import historical_probs as hp; hp._EMPIRICAL_TABLE = None
        parts.append(ground.score_candidates(sub))
    scored = pd.concat(parts, ignore_index=True).dropna(subset=['G', 'DKL']).copy()
    scored['expiry_close'] = scored.apply(lambda r: expiry_close.get((r['ticker'], r['expiry_date'])), axis=1)
    ok = scored.dropna(subset=['expiry_close']).copy()
    ok['width'] = ok['net_credit'] + ok['max_loss']
    def _oc(r):
        sp, ss, ls = r['expiry_close'], r['short_strike'], r['long_strike']
        if r['spread_type'] == 'bull_put':
            return 'WIN' if sp > ss else ('LOSS' if sp <= ls else 'PARTIAL')
        return 'WIN' if sp < ss else ('LOSS' if sp >= ls else 'PARTIAL')
    ok['_outcome'] = ok.apply(_oc, axis=1)
    credit = ok['net_credit'] * FILL; ml = ok['width'] - credit
    pnl = ok.apply(lambda r, c=credit, m=ml: spreads.calc_pnl(
        r['expiry_close'], r['short_strike'], r['long_strike'],
        c.loc[r.name], m.loc[r.name], r['spread_type']), axis=1) * 100
    mask = (ok['_outcome'] == 'PARTIAL') & (pnl > 0); pnl[mask] *= 0.5
    ok['pnl_80'] = pnl
    ok['credit_ratio'] = ok['net_credit'] / ok['max_loss']
    keep = ['entry_date', 'expiry_date', 'ticker', 'spread_type', 'short_strike', 'long_strike',
            'net_credit', 'max_loss', 'width', 'G', 'DKL', 'expiry_close', '_outcome', 'w_star',
            'pnl_80', 'credit_ratio']
    return ok[keep]


def build():
    if os.path.exists(CACHE):
        print(f'Loading {CACHE}'); return pd.read_parquet(CACHE)
    pool = er.load_master_pool(); print(f'pool {len(pool):,} rows', flush=True)
    parts = []
    for y in [2020, 2021, 2022, 2023, 2024, 2025]:
        print(f'-- {y} --', flush=True); r = score_year(y, pool)
        print(f'   {len(r):,} scored', flush=True); parts.append(r)
    c = pd.concat([p for p in parts if not p.empty], ignore_index=True)
    c.to_parquet(CACHE); print(f'wrote {CACHE}: {len(c):,}')
    return c


def evaluate(R, gate):
    R = R.copy(); R['entry_date'] = pd.to_datetime(R['entry_date'])
    spy = pd.read_csv(SPY_CSV, parse_dates=['Date']); td = set(spy['Date'].dt.normalize())
    R = R[R['entry_date'].dt.normalize().isin(td)]
    R = R[R['credit_ratio'] >= gate]                       # <-- the gate
    R['GROUND'] = (np.exp(R['G']) - 1.0) * np.exp(-K_VAL * R['DKL'])
    sel = (R[R['GROUND'] >= THR].sort_values(['entry_date', 'GROUND'], ascending=[True, False])
           .groupby('entry_date').head(5)).copy()
    if sel.empty: return None
    spy = spy.sort_values('Date')
    spy = spy[(spy['Date'] >= sel['entry_date'].min()) & (spy['Date'] <= pd.Timestamp('2025-12-31'))]
    tdi = pd.DatetimeIndex(spy['Date'])
    daily = sel.groupby(pd.to_datetime(sel['expiry_date']))['pnl_80'].sum()
    eq = START + daily.reindex(tdi, fill_value=0.0).cumsum()
    weekly = eq.resample('W-FRI').last().ffill().pct_change().dropna()
    wsd = weekly.std(ddof=0); wsh = float(weekly.mean() * np.sqrt(52) / wsd) if wsd > 0 else 0.0
    peak = eq.cummax(); dd = 100 * float(((eq - peak) / peak).min())
    final = float(eq.iloc[-1]); ny = max((tdi[-1] - tdi[0]).days / 365.25, 1e-9)
    cagr = 100 * ((final / START) ** (1 / ny) - 1) if final > 0 else -100
    return dict(gate=gate, n=len(sel), win=100 * (sel['pnl_80'] > 0).mean(),
                per_tr=sel['pnl_80'].mean(), final=final, cagr=cagr, sh_wk=wsh, dd=dd,
                credit=sel['net_credit'].mean(), ratio=sel['credit_ratio'].median())


if __name__ == '__main__':
    R = build()
    print(f'\n{"="*94}\nMIN_CREDIT_RATIO sweep — delta-20, k=10, thr=0.05, top-5/day, 0.80x mid, 2020-2025\n{"="*94}')
    print(f'  {"gate":>5} {"picks":>6} {"win%":>6} {"P&L/ctr":>9} {"med.ratio":>9} {"credit":>7} {"final(q1)":>11} {"CAGR":>7} {"Sh(wk)":>7} {"MaxDD":>7}')
    for g in GATES:
        m = evaluate(R, g)
        if not m: print(f'  {g:>5.2f}  no trades'); continue
        print(f'  {g:>5.2f} {m["n"]:>6,} {m["win"]:>5.1f}% {m["per_tr"]:>9.2f} {m["ratio"]:>9.2f} '
              f'${m["credit"]:>6.2f} ${m["final"]:>10,.0f} {m["cagr"]:>6.1f}% {m["sh_wk"]:>+7.2f} {m["dd"]:>6.1f}%')
    print(f'\n  (gate 0.30 = current canon; lower gates were NOT previously validated)')
