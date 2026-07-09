"""Backtest variant: treat any non-clean-win as full max-loss.
Models "close early at worst case for any spread not clearly OTM at Friday close."

Outcomes per pick at expiry:
  - bull_put: spot ≥ short_strike  → WIN (full credit)
              spot <  short_strike  → FULL LOSS (max_loss × qty)
  - bear_call: spot ≤ short_strike  → WIN (full credit)
               spot >  short_strike → FULL LOSS

Compares to canonical (partial-payoff-honored) at ¹⁄₁₆ Kelly cap=5, $10k base.
"""
import sys, math
import numpy as np, pandas as pd
sys.path.insert(0, '.')
import config as bt_config
import spreads

YEARS = [2022, 2023, 2024, 2025]
SPY_CSV = 'data/spy_us_d.csv'
START_BANKROLL = 10_000.0
KELLY_FRAC = 0.0625
KELLY_CAP = 5

PICKS_CACHE = 'output/picks_cache_k30.parquet'
print(f'Loading cached picks from {PICKS_CACHE}...')
picks = pd.read_parquet(PICKS_CACHE)
print(f'  {len(picks):,} picks loaded')


def calc_qty(row):
    ws = row.get('w_star'); ml_d = row['max_loss_dollar']
    if ws is None or pd.isna(ws) or ws <= 0 or ml_d <= 0: return 1
    return max(1, min(KELLY_CAP, int(KELLY_FRAC * float(ws) * START_BANKROLL / ml_d)))


def calc_pnl_strict(row):
    """Full credit if clean OTM winner, else full max_loss (no partials)."""
    spot = row['expiry_close']; ss = row['short_strike']; ls = row['long_strike']
    credit = row['credit']; ml = row['max_loss_adj']
    if row['spread_type'] == 'bull_put':
        is_win = spot >= ss
    else:  # bear_call
        is_win = spot <= ss
    return (credit if is_win else -ml) * 100


picks['qty'] = picks.apply(calc_qty, axis=1)
# Canonical (with partials) — already computed in cache as pnl_per_contract
picks['pnl_canon']  = picks['qty'] * picks['pnl_per_contract']
# Strict variant — treat partials as full loss
picks['pnl_strict'] = picks['qty'] * picks.apply(calc_pnl_strict, axis=1)

# Per-pick comparison
def classify(row):
    spot = row['expiry_close']; ss = row['short_strike']; ls = row['long_strike']
    if row['spread_type'] == 'bull_put':
        if spot >= ss: return 'WIN'
        if spot <= ls: return 'MAX_LOSS'
        return 'BETWEEN'
    else:
        if spot <= ss: return 'WIN'
        if spot >= ls: return 'MAX_LOSS'
        return 'BETWEEN'
picks['outcome'] = picks.apply(classify, axis=1)

print(f'\n══ Outcome distribution (2022-2025) ══')
counts = picks['outcome'].value_counts()
for o in ['WIN','BETWEEN','MAX_LOSS']:
    n = int(counts.get(o, 0))
    pct = 100*n/len(picks)
    bar = '█' * int(pct * 0.5)
    print(f'  {o:<9} {n:>4} ({pct:>5.1f}%) {bar}')


def stats(pnl_series, entry_dates):
    n = len(pnl_series); tot = pnl_series.sum(); mu = pnl_series.mean()
    win = 100*(pnl_series > 0).mean()
    df = pd.DataFrame({'entry':pd.to_datetime(entry_dates),'pnl':pnl_series})
    daily = df.groupby('entry')['pnl'].sum().sort_index()
    sg = daily.std(ddof=0); sh = (daily.mean()*np.sqrt(252)/sg) if sg>0 else 0
    return n, tot, mu, win, sh


print(f'\n══ Canonical (partials honored) vs Strict (partials = full loss) ══')
print(f'{"variant":<20} {"trades":>5} {"profit":>10} {"$/tr":>7} {"win %":>6} {"Sharpe":>8}')
print('-'*60)
for name, col in [('Canonical', 'pnl_canon'), ('Strict (no partials)', 'pnl_strict')]:
    n, tot, mu, win, sh = stats(picks[col], picks['entry_date'])
    print(f'  {name:<20} {n:>5} ${tot:>+8,.0f} ${mu:>+5.2f} {win:>5.1f}% Sh{sh:>+5.2f}')

print(f'\n══ Cost of always-close-early ══')
diff = picks['pnl_strict'].sum() - picks['pnl_canon'].sum()
between_picks = picks[picks['outcome']=='BETWEEN']
print(f'  BETWEEN picks: {len(between_picks)}')
print(f'  Their canonical P&L: ${(between_picks["pnl_canon"]).sum():+,.0f}')
print(f'  Their strict P&L:    ${(between_picks["pnl_strict"]).sum():+,.0f}')
print(f'  Total cost (canon − strict): ${diff*-1:+,.0f}')
print(f'  Per year: ${diff*-1/4:+,.0f}/yr')


# Equity curve under strict rule
print(f'\n══ Equity curves (¹⁄₁₆K cap=5, $10k base) ══')
picks['realize'] = pd.to_datetime(picks['expiry_date'])
def cagr(final, n):
    return ((final/START_BANKROLL)**(1/n) - 1) if final > 0 else -1
def max_dd(eq):
    peak = eq.cummax() if hasattr(eq,'cummax') else pd.Series(eq).cummax()
    return float(((eq-peak)/peak).min())
def sharpe(ret): return ret.mean()*np.sqrt(252)/ret.std(ddof=0) if ret.std(ddof=0) > 0 else 0.0

spy = pd.read_csv(SPY_CSV, parse_dates=['Date']).sort_values('Date').reset_index(drop=True)
spy = spy[(spy['Date']>=pd.Timestamp(f'{YEARS[0]}-01-01'))&(spy['Date']<=pd.Timestamp(f'{YEARS[-1]}-12-31'))].reset_index(drop=True)
trading_days = pd.DatetimeIndex(spy['Date'])
n_years = (trading_days[-1]-trading_days[0]).days/365.25

for label, col in [('Canonical', 'pnl_canon'), ('Strict', 'pnl_strict')]:
    daily = picks.groupby('realize')[col].sum().sort_index()
    eq = START_BANKROLL + daily.reindex(trading_days, fill_value=0.0).cumsum()
    ret = eq.diff().fillna(0)/eq.shift(1).fillna(START_BANKROLL)
    print(f'  {label:<10}  ${eq.iloc[-1]:>9,.0f}  CAGR {cagr(eq.iloc[-1], n_years)*100:+5.1f}%  Sharpe {sharpe(ret):+.2f}  MaxDD {max_dd(eq)*100:.1f}%')
