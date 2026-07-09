"""Generate equity curve JSON for the backtest tab.

Strategy (canon 2026-06-09): G_rv GROUND — G's Kelly probs from BS N(d2) at
clamped 10d RV (same P as DKL's belief side), DKL = D(P_rv || Q_iv), k=10,
Gamma = G / exp(k*DKL) >= 0.075, top-5 per entry day, Mon-Thu, SP100,
0.80 x BBO-clamped LAST credit, true-payoff settlement at expiry close.
Source: output/sweep_rv_vs_iv_scored_NOREGIME.parquet (regime gate OFF).
Benchmark: SPY buy-and-hold. Both start at $10,000.

Output: analysis/output/equity_curve_slim.json
  { "config": {...}, "summary": {...}, "points": [{date, strategy, thuonly, spy}] }

NOTE: this writes a SLIM payload. The live backtest tab is fed by
report_three_sizings.py, which writes the rich live/data/backtest_equity.json
(weeks/trades/qty1/sixteenk arms). Never point this script at that path.
"""
import json, math, os
import numpy as np
import pandas as pd
from math import erf

K = 10.0
THR = 0.075
LAST_PCT = 0.80
WINDOW_START = pd.Timestamp('2020-01-01')
SPY_CSV = 'data/spy_us_d.csv'
START_BANKROLL = 10_000.0
OUT_PATH = 'analysis/output/equity_curve_slim.json'

df = pd.read_parquet('output/sweep_rv_vs_iv_scored_NOREGIME.parquet')
df = df.dropna(subset=['G', 'DKL', 'raw_last', 'expiry_close', 'rv_30d',
                       'entry_price', 'net_credit', 'max_loss']).copy()
df = df[(df['rv_30d'] > 0) & (df['max_loss'] > 0) & (df['net_credit'] > 0)]
df['entry_date'] = pd.to_datetime(df['entry_date'])
df = df[df['entry_date'] >= WINDOW_START]
print(f'{len(df)} candidates from {df.entry_date.min().date()} '
      f'to {df.entry_date.max().date()}')

# --- G_rv: Kelly G with N(d2)-at-RV probabilities (ground.py PROB_BASIS="rv") ---
S = df['entry_price'].to_numpy(float)
KS = df['short_strike'].to_numpy(float)
KL_ = df['long_strike'].to_numpy(float)
T = np.maximum(df['DTE'].to_numpy(float), 1.0) / 365.0
is_put = (df['spread_type'] == 'bull_put').to_numpy()
b = df['net_credit'].to_numpy(float) / df['max_loss'].to_numpy(float)
a_par = np.where(b >= 1.0, 0.0, (b - 1.0) / (2.0 * b))
rv = np.clip(df['rv_30d'].to_numpy(float), 0.05, 2.0)


def ncdf(x):
    return 0.5 * (1.0 + np.vectorize(erf)(x / np.sqrt(2.0)))


sT = rv * np.sqrt(T)
d2s = (np.log(S / KS) - 0.5 * rv ** 2 * T) / sT
d2l = (np.log(S / KL_) - 0.5 * rv ** 2 * T) / sT
A_ = np.where(is_put, ncdf(-d2s), ncdf(d2s))
B_ = np.where(is_put, ncdf(-d2l), ncdf(d2l))
B_ = np.minimum(B_, A_)
p = 1.0 - A_
q = B_
ro = np.maximum(0.0, A_ - B_)
s3 = p + q + ro
p, q, ro = p / s3, q / s3, ro / s3

A = -a_par * b * b
Bq = a_par * b * b * (p + ro) - b * (p + ro * a_par + q * (1 + a_par))
C = p * b + ro * a_par * b - q
w_lin = np.where(b > 0, (p * b - q) / np.maximum(b, 1e-12), np.nan)
disc = Bq * Bq - 4 * A * C
with np.errstate(invalid='ignore', divide='ignore'):
    sq = np.sqrt(np.maximum(disc, 0.0))
    r1 = (-Bq - sq) / (2 * A)
    r2 = (-Bq + sq) / (2 * A)
w = np.where((r1 > 0) & (r1 < 1), r1, np.where((r2 > 0) & (r2 < 1), r2, np.nan))
w = np.where(A == 0, w_lin, w)
w = np.where(disc < 0, np.nan, w)
w = np.clip(w, 0.01, 0.99)
with np.errstate(invalid='ignore'):
    G_rv = (p * np.log(np.maximum(1.0 + w * b, 1e-10))
            + ro * np.log(np.maximum(1.0 + w * a_par * b, 1e-10))
            + q * np.log(np.maximum(1.0 - w, 1e-10)))
df['G_rv'] = np.where(np.isnan(w), np.nan, G_rv)
df['GAMMA'] = df['G_rv'] / np.exp(K * df['DKL'])
df = df[df['GAMMA'].notna()].copy()

sel = df[df['GAMMA'] >= THR]
sel = (sel.sort_values(['entry_date', 'GAMMA'], ascending=[True, False])
          .groupby('entry_date').head(5)).copy()
print(f'{len(sel)} picks on {sel.entry_date.nunique()} entry days')

# --- true-payoff settlement at 0.80 x clamped LAST ---
import sys
sys.path.insert(0, '.')
import spreads

sel['credit'] = sel['raw_last'] * LAST_PCT
sel['max_loss_adj'] = sel['width'] - sel['credit']
sel['pnl'] = sel.apply(lambda r: spreads.calc_pnl(
    r['expiry_close'], r['short_strike'], r['long_strike'],
    r['credit'], r['max_loss_adj'], r['spread_type']), axis=1) * 100
sel['realize_date'] = pd.to_datetime(sel['realize_date'])
print(f'avg ${sel.pnl.mean():.2f}/pick  total ${sel.pnl.sum():,.0f}')

monthu = sel
thuonly = sel[sel['entry_date'].dt.dayofweek == 3]
daily = monthu.groupby('realize_date')['pnl'].sum().sort_index()
daily_thu = thuonly.groupby('realize_date')['pnl'].sum().sort_index()

spy = pd.read_csv(SPY_CSV, parse_dates=['Date']).sort_values('Date').reset_index(drop=True)
window_end = sel['realize_date'].max()
spy = spy[(spy['Date'] >= WINDOW_START) & (spy['Date'] <= window_end)].reset_index(drop=True)
trading_days = pd.DatetimeIndex(spy['Date'])

strategy_equity = START_BANKROLL + daily.reindex(trading_days, fill_value=0.0).cumsum()
thuonly_equity = START_BANKROLL + daily_thu.reindex(trading_days, fill_value=0.0).cumsum()
spy_equity = START_BANKROLL * (spy['Close'].values / spy['Close'].iloc[0])

points = [{'date': d.strftime('%Y-%m-%d'),
           'strategy': round(float(strategy_equity.iloc[i]), 2),
           'thuonly': round(float(thuonly_equity.iloc[i]), 2),
           'spy': round(float(spy_equity[i]), 2)}
          for i, d in enumerate(trading_days)]


def sharpe(returns):
    if returns.std(ddof=0) == 0: return 0.0
    return returns.mean() * np.sqrt(252) / returns.std(ddof=0)


def max_dd(equity):
    eq = pd.Series(equity)
    return float(((eq - eq.cummax()) / eq.cummax()).min())


strategy_returns = strategy_equity.diff().fillna(0) / strategy_equity.shift(1).fillna(START_BANKROLL)
thuonly_returns = thuonly_equity.diff().fillna(0) / thuonly_equity.shift(1).fillna(START_BANKROLL)
spy_returns = pd.Series(spy_equity).diff().fillna(0) / pd.Series(spy_equity).shift(1).fillna(START_BANKROLL)

n_years = (trading_days[-1] - trading_days[0]).days / 365.25


def cagr(final):
    return ((final / START_BANKROLL) ** (1 / n_years) - 1) if final > 0 else -1


summary = {
    'window_start': trading_days[0].strftime('%Y-%m-%d'),
    'window_end': trading_days[-1].strftime('%Y-%m-%d'),
    'years': round(n_years, 2),
    'trading_days': len(trading_days),
    'n_trades': int(len(monthu)),
    'n_trades_thuonly': int(len(thuonly)),
    'strategy_final': round(float(strategy_equity.iloc[-1]), 2),
    'thuonly_final': round(float(thuonly_equity.iloc[-1]), 2),
    'spy_final': round(float(spy_equity[-1]), 2),
    'strategy_total_return': round(float((strategy_equity.iloc[-1] - START_BANKROLL) / START_BANKROLL * 100), 2),
    'thuonly_total_return': round(float((thuonly_equity.iloc[-1] - START_BANKROLL) / START_BANKROLL * 100), 2),
    'spy_total_return': round(float((spy_equity[-1] - START_BANKROLL) / START_BANKROLL * 100), 2),
    'strategy_cagr': round(float(cagr(strategy_equity.iloc[-1]) * 100), 2),
    'thuonly_cagr': round(float(cagr(thuonly_equity.iloc[-1]) * 100), 2),
    'spy_cagr': round(float(cagr(spy_equity[-1]) * 100), 2),
    'strategy_sharpe': round(float(sharpe(strategy_returns)), 2),
    'thuonly_sharpe': round(float(sharpe(thuonly_returns)), 2),
    'spy_sharpe': round(float(sharpe(spy_returns)), 2),
    'strategy_max_dd': round(float(max_dd(strategy_equity)) * 100, 2),
    'thuonly_max_dd': round(float(max_dd(thuonly_equity)) * 100, 2),
    'spy_max_dd': round(float(max_dd(spy_equity)) * 100, 2),
}

payload = {
    'config': {
        'universe': 'SP100',
        'days': 'Mon-Thu',
        'expiry': 'Friday (DTE 1-4)',
        'selection': 'top-5 per day, Γ = G_rv·e^(−10·DKL) ≥ 0.075',
        'scoring': 'G_rv: RV-implied N(d2) probs in G; DKL = D(P_rv ‖ Q_iv)',
        'fill_basis': f'{LAST_PCT}×LAST (LAST clamped to BBO per leg)',
        'regime': 'OFF',
        'vol_gate': 'OFF',
        'starting_bankroll': START_BANKROLL,
    },
    'summary': summary,
    'points': points,
}

os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
with open(OUT_PATH, 'w') as f:
    json.dump(payload, f, indent=2)
print(f'\nWrote {OUT_PATH}')
print(f'  Mon-Thu  ${summary["strategy_final"]:,.0f} ({summary["strategy_total_return"]:+.1f}%)  Sharpe {summary["strategy_sharpe"]:+.2f}  CAGR {summary["strategy_cagr"]:+.1f}%  MaxDD {summary["strategy_max_dd"]:.1f}%')
print(f'  Thu-only ${summary["thuonly_final"]:,.0f} ({summary["thuonly_total_return"]:+.1f}%)  Sharpe {summary["thuonly_sharpe"]:+.2f}  CAGR {summary["thuonly_cagr"]:+.1f}%  MaxDD {summary["thuonly_max_dd"]:.1f}%')
print(f'  SPY      ${summary["spy_final"]:,.0f} ({summary["spy_total_return"]:+.1f}%)  Sharpe {summary["spy_sharpe"]:+.2f}  CAGR {summary["spy_cagr"]:+.1f}%  MaxDD {summary["spy_max_dd"]:.1f}%')
