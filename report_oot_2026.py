"""2026 OUT-OF-TIME equity curve under frozen canon (adopted 2026-06-09 on 2020-25 data).
Identical pipeline to report_three_sizings.py, scoped to 2026 Jan-Jun.
Writes live/data/oot_equity.json (NEW file — never touches backtest_equity.json).
"""
import sys, math, json, os
import numpy as np, pandas as pd
sys.path.insert(0, '.')
import config as bt_config
import spreads, ground
import empirical_runner as er

SP100 = set(bt_config.SP100_TICKERS)
YEARS = [2026]
SPY_CSV = 'data/spy_us_d.csv'
K_VAL = ground.DKL_K
START_BANKROLL = 10_000.0
THRESH_BY_DOW = {0: bt_config.GROUND_THRESHOLD, 1: bt_config.GROUND_THRESHOLD,
                 2: bt_config.GROUND_THRESHOLD, 3: bt_config.GROUND_THRESHOLD}
DOW_NAMES = {0:'Mon', 1:'Tue', 2:'Wed', 3:'Thu'}
ACTIVE_DOWS = [0, 1, 2, 3]
OUT_PATH = 'live/data/oot_equity.json'
MIN_ENTRY_DATE = pd.Timestamp('2026-01-01')
YEAR_PARQUET = 'output/2026_sp500_last_oot_combined.parquet'
if not os.path.exists(YEAR_PARQUET):
    YEAR_PARQUET = 'output/2026_sp500_last_oot_refresh.parquet'
if not os.path.exists(YEAR_PARQUET):
    YEAR_PARQUET = 'output/2026_sp500_last.parquet'


def load_spy_daily():
    frames = [pd.read_csv(SPY_CSV, parse_dates=['Date'])]
    try:
        frames.append(pd.read_csv('data/spy_2026_refresh.csv', parse_dates=['Date']))
    except FileNotFoundError:
        pass
    yahoo_spy = 'data/daily_bars_yahoo/SPY.csv'
    if os.path.exists(yahoo_spy):
        y = pd.read_csv(yahoo_spy, header=None,
                        names=['Date', 'Open', 'High', 'Low', 'Close'],
                        parse_dates=['Date'])
        frames.append(y[['Date', 'Open', 'High', 'Low', 'Close']])
    spy = pd.concat(frames, ignore_index=True)
    spy['Date'] = pd.to_datetime(spy['Date'], errors='coerce')
    spy = spy.dropna(subset=['Date'])
    for col in ['Open', 'High', 'Low', 'Close']:
        if col in spy.columns:
            spy[col] = pd.to_numeric(spy[col], errors='coerce')
    spy = spy.dropna(subset=['Close'])
    return (spy.drop_duplicates(subset='Date', keep='last')
               .sort_values('Date')
               .reset_index(drop=True))

# Per-(Symbol, DataDate) IV-rank bucket lookup, written by build_production_pool.
IV_RANK_LOOKUP = None
try:
    _ivr = pd.read_parquet('output/iv_rank.parquet')
    _ivr['DataDate'] = pd.to_datetime(_ivr['DataDate'])
    IV_RANK_LOOKUP = _ivr[['Symbol', 'DataDate', 'iv_rank_bucket']]
    print(f'Loaded IV-rank lookup: {len(IV_RANK_LOOKUP):,} entries')
except FileNotFoundError:
    print('WARNING: output/iv_rank.parquet not found — IV-rank disabled (fallback to 3-tuple buckets)')

# Per-(Symbol, DataDate) 30d realized vol lookup, for PROB_BASIS='rv' tests.
RV_LOOKUP = None
try:
    _rv = pd.read_parquet('output/rv_table.parquet')
    _rv['DataDate'] = pd.to_datetime(_rv['DataDate'])
    RV_LOOKUP = _rv[['Symbol', 'DataDate', 'rv_30d']]
    print(f'Loaded RV lookup: {len(RV_LOOKUP):,} entries')
except FileNotFoundError:
    print('WARNING: output/rv_table.parquet not found — PROB_BASIS=rv will be inactive')

# Load master pool once for rolling empirical lookup
POOL = er.load_master_pool()
print(f'Loaded master pool: {len(POOL):,} rows', flush=True)


def score_year(year):
    df_full = pd.read_parquet(YEAR_PARQUET)
    df_full = df_full[df_full['Symbol'].isin(SP100)]
    df_full['PutCall'] = df_full['PutCall'].str.lower().str.strip()
    expiry_close = (df_full[df_full['DataDate']==df_full['ExpirationDate']]
                    .groupby(['Symbol','ExpirationDate'])['UnderlyingPrice']
                    .first().to_dict())
    spy_dates = set(load_spy_daily()['Date'].dt.normalize())
    df = df_full.copy()
    df['dow']     = df['DataDate'].dt.dayofweek
    df['exp_dow'] = df['ExpirationDate'].dt.dayofweek
    df = df[df['dow'].isin(ACTIVE_DOWS)]
    df = df[df['exp_dow']==4]
    df = df[(df['DTE']>=1)&(df['DTE']<=4)]
    df = df[df['LastPrice'].astype(float) > 0]
    df = df[df['DataDate'] >= MIN_ENTRY_DATE]
    df = df[df['DataDate'].dt.normalize().isin(spy_dates)]
    df = df.copy()
    df['AbsDelta'] = df['Delta'].abs()
    df['MidPrice'] = (df['BidPrice'] + df['AskPrice']) / 2.0
    # Attach per-(Symbol, DataDate) IV-rank bucket so build_candidates carries it
    # through to each candidate row → ground.py passes to empirical_lookup_probs.
    if IV_RANK_LOOKUP is not None:
        df = df.merge(IV_RANK_LOOKUP, on=['Symbol', 'DataDate'], how='left')
    if RV_LOOKUP is not None:
        df = df.merge(RV_LOOKUP, on=['Symbol', 'DataDate'], how='left')

    spreads.REGIME_LOOKUP = spreads.build_regime_lookup(SPY_CSV, sma_window=100)
    spreads.REGIME_FILTER = False  # canonical 2026-06-05: regime gate OFF (Sh 2.19 vs 1.48 with gate)
    spreads.REGIME_PER_TICKER = False
    spreads.GAP_FILTER = False
    spreads.LOW_VIX_BULLPUT_FILTER = False
    spreads.SLIPPAGE_CENTS = 0.0
    bt_config.MIN_OPEN_INTEREST = 100
    bt_config.CREDIT_BASIS = "mid"
    bt_config.CREDIT_SCALE = 1.0

    candidates = spreads.build_candidates(df)

    # Per-date rolling empirical lookup (put/call split, 50w trailing window)
    parts = []
    dates = sorted(candidates['entry_date'].unique())
    for dt in dates:
        sub = candidates[candidates['entry_date'] == dt]
        if sub.empty: continue
        ok = er.install_window(POOL, pd.Timestamp(dt))
        if not ok:
            import historical_probs as hp
            hp._EMPIRICAL_TABLE = None  # forces uniform-DKL fallback
        parts.append(ground.score_candidates(sub))
    scored = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()

    def gnd(r):
        G, DKL = r.get('G'), r.get('DKL')
        if G is None or DKL is None or pd.isna(G) or pd.isna(DKL): return float('nan')
        return (math.exp(G)-1.0) * math.exp(-K_VAL * DKL)
    scored['GROUND'] = scored.apply(gnd, axis=1)
    return scored.dropna(subset=['GROUND']), expiry_close


def realize(scored, expiry_close):
    s = scored.copy()
    s['entry_dow'] = pd.to_datetime(s['entry_date']).dt.dayofweek
    parts = []
    for dow in ACTIVE_DOWS:
        thr = THRESH_BY_DOW[dow]
        sub = s[s['entry_dow'] == dow]
        qual = sub[sub['GROUND'] >= thr]
        top = qual.sort_values(['entry_date','GROUND'], ascending=[True,False]).groupby('entry_date').head(5)
        parts.append(top)
    sel = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
    if sel.empty: return pd.DataFrame(columns=['entry_date','realize_date','pnl_per_contract','max_loss_dollar'])
    def lookup(row):
        return expiry_close.get((row['ticker'], row['expiry_date']))
    sel['expiry_close'] = sel.apply(lookup, axis=1)
    ok = sel.dropna(subset=['expiry_close']).copy()
    ok['credit'] = ok['net_credit'] * 0.80  # canonical: realized fills modeled at 0.80 x raw combo mid
    ok['width']  = ok['net_credit'] + ok['max_loss']
    ok['max_loss_adj'] = ok['width'] - ok['credit']
    ok['pnl_per_contract'] = ok.apply(lambda r: spreads.calc_pnl(
        r['expiry_close'], r['short_strike'], r['long_strike'],
        r['credit'], r['max_loss_adj'], r['spread_type']), axis=1) * 100
    ok['max_loss_dollar'] = ok['max_loss_adj'] * 100  # per contract
    ok['realize_date'] = pd.to_datetime(ok['expiry_date'])
    ok['entry_date_dt'] = pd.to_datetime(ok['entry_date'])
    return ok


_cache_tag = os.path.splitext(os.path.basename(YEAR_PARQUET))[0].replace('2026_sp500_last_', '')
CACHE_PATH = f'output/picks_cache_oot2026_{_cache_tag}_grv_k{K_VAL:g}_thr{bt_config.GROUND_THRESHOLD:g}_mid.parquet'
if os.path.exists(CACHE_PATH):
    print(f'Loading cached picks from {CACHE_PATH}...')
    all_picks = pd.read_parquet(CACHE_PATH)
    all_picks['entry_date_dt'] = pd.to_datetime(all_picks['entry_date_dt'])
    print(f'  {len(all_picks):,} picks loaded (skip --no-cache to force re-score)')
else:
    print(f'Building picks for k={K_VAL:g}, threshold={bt_config.GROUND_THRESHOLD:g}, Mon-Thu...')
    all_picks_per_year = []
    for year in YEARS:
        print(f'── {year} ──', flush=True)
        scored, expiry_close = score_year(year)
        all_picks_per_year.append(realize(scored, expiry_close))
    all_picks = pd.concat(all_picks_per_year, ignore_index=True).sort_values('entry_date_dt').reset_index(drop=True)
    print(f'  {len(all_picks):,} picks total — caching to {CACHE_PATH}')
    all_picks.to_parquet(CACHE_PATH)

# Realistic partial-win haircut (canonical 2026-06-08): partial outcomes are
# harder to capture live due to pin/assignment risk + early-close slippage.
# Discount partial WINS to 50% of intrinsic; keep partial LOSSES at 100%
# (you can't beat them). Reduces final by ~$4.7k but more honest.
def _outcome_class(r):
    sp, ss, ls = r['expiry_close'], r['short_strike'], r['long_strike']
    if r['spread_type'] == 'bull_put':
        if sp > ss:  return 'WIN'
        if sp <= ls: return 'LOSS'
        return 'PARTIAL'
    else:
        if sp < ss:  return 'WIN'
        if sp >= ls: return 'LOSS'
        return 'PARTIAL'
all_picks['_outcome'] = all_picks.apply(_outcome_class, axis=1)
_mask_pwin = (all_picks['_outcome'] == 'PARTIAL') & (all_picks['pnl_per_contract'] > 0)
print(f'  partial-WIN haircut: discounting {_mask_pwin.sum()} picks to 50% intrinsic')
all_picks.loc[_mask_pwin, 'pnl_per_contract'] *= 0.5


# Simulate each sizing variant chronologically
def simulate_equity(picks, sizing):
    """sizing = int (1, 2) | 'dyn10k' | 'kelly_X' where X is the Kelly fraction (e.g. 1.0, 0.5, 0.25).
    Kelly: contracts = max(1, floor(X * w_star * bankroll / max_loss_dollar))."""
    bankroll = START_BANKROLL
    daily_pnl = {}
    for _, row in picks.iterrows():
        if sizing == 'dyn10k':
            qty = max(1, min(5, int(bankroll // 10000)))
        elif isinstance(sizing, str) and sizing.startswith('kelly_'):
            frac = float(sizing.split('_')[1])
            ws = row.get('w_star')
            ml_dollar = row['max_loss_dollar']
            # Use FIXED $10k reference wallet — no compounding. This isolates the
            # Kelly per-bet sizing effect from the compounding-on-wins effect.
            ref_wallet = START_BANKROLL
            if ws is None or pd.isna(ws) or ws <= 0 or ml_dollar <= 0:
                qty = 1  # Kelly says nothing → take min position
            else:
                kelly_dollars = frac * float(ws) * ref_wallet
                qty = max(1, min(5, int(kelly_dollars / ml_dollar)))
        elif isinstance(sizing, str) and sizing.startswith('gkelly_'):
            # GROUND-as-edge Kelly: w = α × GROUND / b. Uses COMPOUNDING bankroll.
            # b = net_credit / max_loss = payoff ratio.
            frac = float(sizing.split('_')[1])
            G  = row.get('GROUND')
            nc = row.get('net_credit')
            ml = row.get('max_loss')
            ml_dollar = row['max_loss_dollar']
            if G is None or pd.isna(G) or G <= 0 or nc is None or ml is None or ml_dollar <= 0:
                qty = 1
            else:
                b = float(nc) / float(ml) if ml > 0 else 0
                if b <= 0:
                    qty = 1
                else:
                    w = frac * float(G) / b
                    kelly_dollars = w * bankroll
                    qty = max(1, min(5, int(kelly_dollars / ml_dollar)))
        else:
            qty = int(sizing)
        pnl = qty * row['pnl_per_contract']
        rd = row['realize_date']
        daily_pnl[rd] = daily_pnl.get(rd, 0.0) + pnl
        bankroll += pnl
        if bankroll <= 0:
            bankroll = 1  # don't go negative; floor at $1 so Kelly still works
    return pd.Series(daily_pnl).sort_index()


pnl_qty2      = simulate_equity(all_picks, '2')       # canonical 2026-06-03
pnl_qty1      = simulate_equity(all_picks, '1')
pnl_sixteenk  = simulate_equity(all_picks, 'kelly_0.0625')


# Build equity curves aligned to SPY trading days
spy = load_spy_daily()
spy = spy.sort_values('Date').reset_index(drop=True)
# Anchor curve start to the first actual entry date (not Jan 1 of YEARS[0])
# so we don't show months of flat $10k line before the strategy began.
start_date = all_picks['entry_date_dt'].min().normalize()
spy = spy[(spy['Date'] >= start_date) & (spy['Date'] <= pd.Timestamp(f'{YEARS[-1]}-12-31'))].reset_index(drop=True)
trading_days = pd.DatetimeIndex(spy['Date'])

eq_qty2     = START_BANKROLL + pnl_qty2.reindex(trading_days, fill_value=0.0).cumsum()
eq_qty1     = START_BANKROLL + pnl_qty1.reindex(trading_days, fill_value=0.0).cumsum()
eq_sixteenk = START_BANKROLL + pnl_sixteenk.reindex(trading_days, fill_value=0.0).cumsum()
spy_eq      = START_BANKROLL * (spy['Close'].values / spy['Close'].iloc[0])


def sharpe(ret): return ret.mean()*np.sqrt(252)/ret.std(ddof=0) if ret.std(ddof=0) > 0 else 0.0
def max_dd(eq):
    peak = eq.cummax() if hasattr(eq,'cummax') else pd.Series(eq).cummax()
    return float(((eq-peak)/peak).min())
n_years = (trading_days[-1] - trading_days[0]).days / 365.25
def cagr(final):
    return ((final/START_BANKROLL)**(1/n_years) - 1) if final > 0 else -1


def summary_for(eq, name):
    if isinstance(eq, pd.Series) and isinstance(eq.index, pd.DatetimeIndex):
        eq_s = eq
    else:
        eq_s = pd.Series(eq if not isinstance(eq, pd.Series) else eq.values, index=trading_days)
    ret = eq_s.diff().fillna(0) / eq_s.shift(1).fillna(START_BANKROLL)
    # Weekly Sharpe — friday-resampled closes, annualized with √52.
    # More comparable to SPY for strategies that resolve mostly on Fridays.
    weekly_eq = eq_s.resample('W-FRI').last().ffill()
    weekly_ret = weekly_eq.pct_change().dropna()
    weekly_sd = weekly_ret.std(ddof=0)
    weekly_sh = float(weekly_ret.mean() * np.sqrt(52) / weekly_sd) if weekly_sd > 0 else 0.0
    final_val = float(eq_s.iloc[-1])
    return {
        f'{name}_final':         round(final_val, 2),
        f'{name}_total_return':  round((final_val - START_BANKROLL) / START_BANKROLL * 100, 2),
        f'{name}_cagr':          round(cagr(final_val) * 100, 2),
        f'{name}_sharpe':        round(float(sharpe(ret)), 2),
        f'{name}_sharpe_weekly': round(weekly_sh, 2),
        f'{name}_max_dd':        round(float(max_dd(eq_s)) * 100, 2),
    }


summary = {
    'window_start':  trading_days[0].strftime('%Y-%m-%d'),
    'window_end':    trading_days[-1].strftime('%Y-%m-%d'),
    'years':         round(n_years, 2),
    'trading_days':  len(trading_days),
    'n_trades':      int(len(all_picks)),
}
summary.update(summary_for(eq_qty2,     'strategy'))   # canonical = qty=2
summary.update(summary_for(eq_qty1,     'qty1'))
summary.update(summary_for(eq_sixteenk, 'sixteenk'))
summary.update(summary_for(pd.Series(spy_eq), 'spy'))

# Yield = total P&L / total wagered (per sizing variant)
ml_dollar_col = all_picks['max_loss_dollar']
wagered_qty2     = float((ml_dollar_col * 2).sum())
wagered_qty1     = float(ml_dollar_col.sum())
def _kelly_qty_row(r, frac=0.0625, cap=5):
    ws = r.get('w_star'); ml_d = r['max_loss_dollar']
    if ws is None or pd.isna(ws) or ws <= 0 or ml_d <= 0: return 1
    return max(1, min(cap, int(frac * float(ws) * START_BANKROLL / ml_d)))
wagered_sixteenk = float((all_picks.apply(_kelly_qty_row, axis=1) * ml_dollar_col).sum())
summary['strategy_wagered'] = round(wagered_qty2, 2)
summary['qty1_wagered']     = round(wagered_qty1, 2)
summary['sixteenk_wagered'] = round(wagered_sixteenk, 2)
summary['strategy_yield']   = round(100.0 * (summary['strategy_final'] - START_BANKROLL) / wagered_qty2,     2) if wagered_qty2 > 0 else 0
summary['qty1_yield']       = round(100.0 * (summary['qty1_final']     - START_BANKROLL) / wagered_qty1,     2) if wagered_qty1 > 0 else 0
summary['sixteenk_yield']   = round(100.0 * (summary['sixteenk_final'] - START_BANKROLL) / wagered_sixteenk, 2) if wagered_sixteenk > 0 else 0

points = []
for i, d in enumerate(trading_days):
    points.append({
        'date':     d.strftime('%Y-%m-%d'),
        'strategy': round(float(eq_qty2.iloc[i]),     2),   # canonical = qty=2
        'qty1':     round(float(eq_qty1.iloc[i]),     2),
        'sixteenk': round(float(eq_sixteenk.iloc[i]), 2),
        'spy':      round(float(spy_eq[i]),           2),
    })

# Build per-week trade log for the backtest tab. Canonical sizing 2026-06-03: qty=2.
def _qty(row):
    return 2
DOW_LONG = {0:'Mon',1:'Tue',2:'Wed',3:'Thu',4:'Fri'}

def _outcome(r):
    if r['spread_type'] == 'bull_put':
        if r['expiry_close'] > r['short_strike']: return 'WIN'
        if r['expiry_close'] <= r['long_strike']: return 'LOSS'
        return 'PARTIAL'
    else:
        if r['expiry_close'] < r['short_strike']: return 'WIN'
        if r['expiry_close'] >= r['long_strike']: return 'LOSS'
        return 'PARTIAL'

sorted_picks = all_picks.sort_values(['entry_date_dt','GROUND'], ascending=[True,False]).copy()
sorted_picks['_q'] = sorted_picks.apply(_qty, axis=1)
sorted_picks['_pnl'] = sorted_picks['_q'] * sorted_picks['pnl_per_contract']
sorted_picks['_week'] = sorted_picks['entry_date_dt'].dt.to_period('W-FRI')

# Bankroll walk
running = START_BANKROLL
sorted_picks['_pre_bank'] = 0.0; sorted_picks['_post_bank'] = 0.0
for idx in sorted_picks.index:
    sorted_picks.at[idx, '_pre_bank'] = running
    running += sorted_picks.at[idx, '_pnl']
    sorted_picks.at[idx, '_post_bank'] = running

def _row_dict(r):
    return {
        'entry':    pd.Timestamp(r['entry_date_dt']).strftime('%Y-%m-%d'),
        'dow':      DOW_LONG.get(pd.Timestamp(r['entry_date_dt']).dayofweek, '?'),
        'ticker':   r['ticker'],
        'type':     r['spread_type'],
        'k_s':      round(float(r['short_strike']), 2),
        'k_l':      round(float(r['long_strike']), 2),
        'credit':   round(float(r['credit']), 4),
        'max_loss': round(float(r['max_loss_adj']), 4),
        'spot':     round(float(r['expiry_close']), 2),
        'qty':      int(r['_q']),
        'pnl':      round(float(r['_pnl']), 2),
        'ground':   round(float(r['GROUND']), 6),
        'dkl':      round(float(r['DKL']), 4) if pd.notna(r.get('DKL')) else None,
        'kelly_ev': round((math.exp(float(r['G']))-1.0)*100, 2) if pd.notna(r.get('G')) else None,
        'outcome':  _outcome(r),
    }

weeks = []
for week_period, grp in sorted_picks.groupby('_week', sort=True):
    grp = grp.sort_values(['entry_date_dt','GROUND'], ascending=[True,False])
    n_bp = int((grp['spread_type'] == 'bull_put').sum())
    n_bc = int((grp['spread_type'] == 'bear_call').sum())
    weeks.append({
        'label':       f'Week of {grp["entry_date_dt"].min().strftime("%b %d, %Y")}',
        'start':       grp['entry_date_dt'].min().strftime('%Y-%m-%d'),
        'n_trades':    int(len(grp)),
        'n_bull_put':  n_bp,
        'n_bear_call': n_bc,
        'direction':   n_bp - n_bc,
        'pnl':         round(float(grp['_pnl'].sum()), 2),
        'credit':      round(float((grp['credit'] * grp['_q']).sum() * 100), 2),
        'risk':        round(float((grp['max_loss_adj'] * grp['_q']).sum() * 100), 2),
        'pre_bank':    round(float(grp['_pre_bank'].iloc[0]), 2),
        'post_bank':   round(float(grp['_post_bank'].iloc[-1]), 2),
        'max_g':       round(float(grp['GROUND'].max()), 6),
        'trades':      [_row_dict(r) for _, r in grp.iterrows()],
    })
trade_log_rows = [t for w in weeks for t in w['trades']]

payload = {
    'config': {
        'universe':   'SP100',
        'days':       'Mon, Tue, Wed, Thu',
        'expiry':     'Friday (DTE 1-4)',
        'selection':  f'top-5 per day, k={K_VAL:g}, GROUND threshold {bt_config.GROUND_THRESHOLD:g} (all days) — FROZEN canon, no 2026 tuning',
        'scoring':    'G_rv: RV-implied N(d2) probs in G (canon 2026-06-09); rv_vs_iv DKL (BS d2, 10d RV vs IV); raw combo MID credit',
        'fill_basis': '0.80×raw combo MID (20% haircut); partial-WIN at 50% intrinsic (pin-risk realistic)',
        'regime':     'OFF (both directions eligible)',
        'vol_gate':   'OFF',
        'sizing':     'qty=2 per pick (canonical 2026-06-05)',
        'starting_bankroll': START_BANKROLL,
    },
    'summary':    summary,
    'points':     points,
    'trades':     trade_log_rows,
    'weeks':      weeks,
}

os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
with open(OUT_PATH, 'w') as f:
    json.dump(payload, f, indent=2)

print(f'\nEquity curve summary:')
for name, label in [('strategy','qty=2 (canon)'), ('qty1','qty=1'), ('sixteenk','¹⁄₁₆ Kelly')]:
    print(f'  {label:<14} ${summary[f"{name}_final"]:>10,.0f}  '
          f'{summary[f"{name}_total_return"]:+7.1f}%  CAGR {summary[f"{name}_cagr"]:+6.1f}%  '
          f'Sharpe {summary[f"{name}_sharpe"]:+.2f}  MaxDD {summary[f"{name}_max_dd"]:.1f}%')
print(f'  {"SPY":<8} ${summary["spy_final"]:>9,.0f}  '
      f'{summary["spy_total_return"]:+6.1f}%  CAGR {summary["spy_cagr"]:+5.1f}%  '
      f'Sharpe {summary["spy_sharpe"]:+.2f}  MaxDD {summary["spy_max_dd"]:.1f}%')
print(f'\nWrote {OUT_PATH}')


# ── Weekly capital deployment at ⅛ Kelly (canonical) ─────────────────
print(f'\n══ Weekly capital deployed (⅛ Kelly, flat $10k base) ══')
weeks = {}
for _, row in all_picks.iterrows():
    ws = row.get('w_star')
    ml_dollar = row['max_loss_dollar']
    if ws is None or pd.isna(ws) or ws <= 0 or ml_dollar <= 0:
        contracts = 1
    else:
        contracts = max(1, min(5, int(0.125 * float(ws) * START_BANKROLL / ml_dollar)))
    capital = contracts * ml_dollar
    # Week = ISO week of expiry date (all picks in a week expire same Friday)
    week = pd.Timestamp(row['realize_date']).strftime('%Y-W%V')
    weeks.setdefault(week, {'capital': 0.0, 'contracts': 0, 'picks': 0})
    weeks[week]['capital']   += capital
    weeks[week]['contracts'] += contracts
    weeks[week]['picks']     += 1

w_df = pd.DataFrame(weeks).T
print(f'  Total weeks with picks: {len(w_df)}')
print(f'  Average capital at risk per week: ${w_df["capital"].mean():>9,.0f}')
print(f'  Median  capital at risk per week: ${w_df["capital"].median():>9,.0f}')
print(f'  Max     capital at risk per week: ${w_df["capital"].max():>9,.0f}')
print(f'  Min     capital at risk per week: ${w_df["capital"].min():>9,.0f}')
print(f'  Average contracts per week: {w_df["contracts"].mean():.1f}')
print(f'  Average picks per week:     {w_df["picks"].mean():.1f}')
print(f'\n  Distribution of weekly capital (bucket → count):')
for lo, hi in [(0,500),(500,1000),(1000,2000),(2000,3000),(3000,5000),(5000,10000),(10000,99999)]:
    n = ((w_df['capital'] >= lo) & (w_df['capital'] < hi)).sum()
    pct = 100*n/len(w_df)
    bar = '█' * int(pct * 0.5)
    label = f'${lo:>5,}-${hi:>5,}' if hi < 99999 else f'${lo:>5,}+        '
    print(f'  {label}  {n:>3} ({pct:>4.1f}%) {bar}')
