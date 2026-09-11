"""Delta-20 fill stress (0.70/0.75/0.80) on 2020-2025, plus 2026 OOT.
Reuses the delta-20 config + scoring from backtest_delta20.py. Single delta
config (0.20, band [0.10,0.30]); k=10, thr=0.05, top-5/day; mid-basis selection.
"""
import sys, os
import numpy as np, pandas as pd
sys.path.insert(0, '.')
import config as bt_config
import spreads, ground
import empirical_runner as er
import backtest_delta20 as d20   # sets DELTA_TARGET=0.20, band [0.10,0.30], loads IV/RV lookups

SPY_CSV = 'data/spy_us_d.csv'
START = 10_000.0
K_VAL, THR = 10.0, 0.05
FILLS = [0.70, 0.75, 0.80]
OOT_PARQUET = 'output/2026_sp500_last_oot_combined.parquet'
OOT_CACHE = 'output/sweep_delta20_oot2026.parquet'


def recompute_pnl(df, fill):
    c = df.copy()
    credit = c['net_credit'] * fill
    ml = c['width'] - credit
    pnl = c.apply(lambda r, cc=credit, mm=ml: spreads.calc_pnl(
        r['expiry_close'], r['short_strike'], r['long_strike'],
        cc.loc[r.name], mm.loc[r.name], r['spread_type']), axis=1) * 100
    mask = (c['_outcome'] == 'PARTIAL') & (pnl > 0)
    pnl[mask] *= 0.5
    return pnl


def select(R):
    R = R.copy(); R['entry_date'] = pd.to_datetime(R['entry_date'])
    spy = pd.read_csv(SPY_CSV, parse_dates=['Date']); td = set(spy['Date'].dt.normalize())
    R = R[R['entry_date'].dt.normalize().isin(td)]
    R['GROUND'] = (np.exp(R['G']) - 1.0) * np.exp(-K_VAL * R['DKL'])
    return (R[R['GROUND'] >= THR].sort_values(['entry_date', 'GROUND'], ascending=[True, False])
            .groupby('entry_date').head(5)).copy()


def metrics(sel, pnl_col, end):
    spy = pd.read_csv(SPY_CSV, parse_dates=['Date']).sort_values('Date')
    spy = spy[(spy['Date'] >= sel['entry_date'].min()) & (spy['Date'] <= end)]
    td = pd.DatetimeIndex(spy['Date'])
    daily = sel.groupby(pd.to_datetime(sel['expiry_date']))[pnl_col].sum()
    eq = START + daily.reindex(td, fill_value=0.0).cumsum()
    weekly = eq.resample('W-FRI').last().ffill().pct_change().dropna()
    wsd = weekly.std(ddof=0)
    wsh = float(weekly.mean() * np.sqrt(52) / wsd) if wsd > 0 else 0.0
    peak = eq.cummax(); dd = 100 * float(((eq - peak) / peak).min())
    final = float(eq.iloc[-1])
    ny = max((td[-1] - td[0]).days / 365.25, 1e-9)
    cagr = 100 * ((final / START) ** (1 / ny) - 1) if final > 0 else -100
    return dict(n=len(sel), final=final, cagr=cagr, sh_wk=wsh, dd=dd,
                win=100 * (sel[pnl_col] > 0).mean(), per_tr=sel[pnl_col].mean())


def stress_table(R, label, end):
    sel = select(R)
    print(f'\n{"="*70}\n{label}   (n={len(sel):,} picks)\n{"="*70}')
    print(f'  {"fill":>5} {"final":>11} {"CAGR":>7} {"Sh(wk)":>7} {"MaxDD":>7} {"win":>6} {"P&L/ctr":>9}')
    for f in FILLS:
        sel[f'pnl_{int(f*100)}'] = recompute_pnl(sel, f)
        m = metrics(sel, f'pnl_{int(f*100)}', end)
        print(f'  {f:>5.2f} ${m["final"]:>10,.0f} {m["cagr"]:>6.1f}% {m["sh_wk"]:>+7.2f} '
              f'{m["dd"]:>6.1f}% {m["win"]:>5.1f}% ${m["per_tr"]:>8.2f}')
    return sel


def build_oot():
    if os.path.exists(OOT_CACHE):
        print(f'Loading OOT cache {OOT_CACHE}')
        return pd.read_parquet(OOT_CACHE)
    d20.POOL = er.load_master_pool()
    print(f'Loaded master pool: {len(d20.POOL):,} rows', flush=True)
    df_full = pd.read_parquet(OOT_PARQUET)
    df_full = df_full[df_full['Symbol'].isin(d20.SP100)]
    df_full['PutCall'] = df_full['PutCall'].str.lower().str.strip()
    expiry_close = (df_full[df_full['DataDate'] == df_full['ExpirationDate']]
                    .groupby(['Symbol', 'ExpirationDate'])['UnderlyingPrice'].first().to_dict())
    df = df_full.copy()
    df['dow'] = df['DataDate'].dt.dayofweek
    df['exp_dow'] = df['ExpirationDate'].dt.dayofweek
    df = df[df['dow'].isin(d20.ACTIVE_DOWS)]
    df = df[df['exp_dow'] == 4]
    df = df[(df['DTE'] >= 1) & (df['DTE'] <= 4)]
    df = df[df['LastPrice'].astype(float) > 0]
    df = df[df['DataDate'] >= pd.Timestamp('2026-01-01')]
    _spy = pd.read_csv(SPY_CSV, parse_dates=['Date'])
    df = df[df['DataDate'].isin(set(_spy['Date']))]
    df = df.copy()
    df['AbsDelta'] = df['Delta'].abs()
    df['MidPrice'] = (df['BidPrice'] + df['AskPrice']) / 2.0
    if d20.IV_RANK_LOOKUP is not None:
        df = df.merge(d20.IV_RANK_LOOKUP, on=['Symbol', 'DataDate'], how='left')
    if d20.RV_LOOKUP is not None:
        df = df.merge(d20.RV_LOOKUP, on=['Symbol', 'DataDate'], how='left')
    spreads.REGIME_LOOKUP = spreads.build_regime_lookup(SPY_CSV, sma_window=100)
    spreads.REGIME_FILTER = spreads.GAP_FILTER = spreads.LOW_VIX_BULLPUT_FILTER = False
    spreads.REGIME_PER_TICKER = False
    spreads.SLIPPAGE_CENTS = 0.0
    bt_config.MIN_OPEN_INTEREST = 100
    cand = spreads.build_candidates(df)
    print(f'  OOT candidates: {len(cand):,}', flush=True)
    parts = []
    for dt in sorted(cand['entry_date'].unique()):
        sub = cand[cand['entry_date'] == dt]
        if sub.empty: continue
        ok = er.install_window(d20.POOL, pd.Timestamp(dt))
        if not ok:
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
    keep = ['entry_date', 'expiry_date', 'ticker', 'spread_type', 'short_strike', 'long_strike',
            'net_credit', 'max_loss', 'width', 'G', 'DKL', 'expiry_close', '_outcome', 'w_star']
    ok = ok[[c for c in keep if c in ok.columns]]
    ok.to_parquet(OOT_CACHE)
    print(f'Wrote {OOT_CACHE}: {len(ok):,} rows')
    return ok


if __name__ == '__main__':
    BT = d20.build_cache()   # existing 2020-25 delta-20 cache
    stress_table(BT, 'DELTA-20 FILL STRESS — 2020-2025', pd.Timestamp('2025-12-31'))
    OOT = build_oot()
    stress_table(OOT, 'DELTA-20 FILL STRESS — 2026 OOT (Jan-Aug)', pd.Timestamp('2026-08-31'))
