"""Generate backtest_equity.json + oot_equity.json under the mid-basis canon
(2026-06-10): selection on raw combo mid, k=10, thr=0.07, fills 0.80 x mid.

Reads the sweep caches (output/sweep_midmkt_*.parquet — already scored and
realized at all fill fractions, partial-WIN haircut applied) so no rescoring
is needed. Payload structure mirrors report_three_sizings.py exactly
(summary/points/weeks/trades + qty1/strategy/sixteenk/spy variants).
"""
import math, json, os
import numpy as np, pandas as pd

K_VAL = 10.0
THR = 0.05
FILL_FRAC = 0.80
PNL_COL = 'pnl_80'
START_BANKROLL = 10_000.0
SPY_CSV = 'data/spy_us_d.csv'
DOW_LONG = {0:'Mon',1:'Tue',2:'Wed',3:'Thu',4:'Fri'}


def select_picks(cache_path):
    R = pd.read_parquet(cache_path)
    R['entry_date'] = pd.to_datetime(R['entry_date'])
    # Exchange-calendar filter (2026-06-11): vendor files republish stale
    # quotes on market holidays (DTE decremented); without this, ~3% of
    # entries land on days the market was closed (error #74).
    _spy = pd.read_csv(SPY_CSV, parse_dates=['Date'])
    _tdays = set(_spy['Date'].dt.normalize())
    R = R[R['entry_date'].dt.normalize().isin(_tdays)].copy()
    R['GROUND'] = (np.exp(R['G']) - 1.0) * np.exp(-K_VAL * R['DKL'])
    sel = (R[R['GROUND'] >= THR]
           .sort_values(['entry_date','GROUND'], ascending=[True,False])
           .groupby('entry_date').head(5)).copy()
    sel['credit'] = (sel['net_credit'] * FILL_FRAC).round(4)
    sel['max_loss_adj'] = (sel['width'] - sel['credit']).round(4)
    sel['pnl_per_contract'] = sel[PNL_COL]
    sel['max_loss_dollar'] = sel['max_loss_adj'] * 100
    sel['realize_date'] = pd.to_datetime(sel['expiry_date'])
    sel['entry_date_dt'] = sel['entry_date']
    return sel.sort_values('entry_date_dt').reset_index(drop=True)


def simulate_equity(picks, sizing):
    bankroll = START_BANKROLL
    daily_pnl = {}
    for _, row in picks.iterrows():
        if isinstance(sizing, str) and sizing.startswith('kelly_'):
            frac = float(sizing.split('_')[1])
            ws = row.get('w_star'); ml_dollar = row['max_loss_dollar']
            if ws is None or pd.isna(ws) or ws <= 0 or ml_dollar <= 0:
                qty = 1
            else:
                qty = max(1, min(5, int(frac * float(ws) * START_BANKROLL / ml_dollar)))
        else:
            qty = int(sizing)
        pnl = qty * row['pnl_per_contract']
        rd = row['realize_date']
        daily_pnl[rd] = daily_pnl.get(rd, 0.0) + pnl
        bankroll += pnl
        if bankroll <= 0: bankroll = 1
    return pd.Series(daily_pnl).sort_index()


def build_payload(picks, end_year, label):
    spy = pd.read_csv(SPY_CSV, parse_dates=['Date']).sort_values('Date').reset_index(drop=True)
    start_date = picks['entry_date_dt'].min().normalize()
    spy = spy[(spy['Date'] >= start_date) & (spy['Date'] <= pd.Timestamp(f'{end_year}-12-31'))].reset_index(drop=True)
    td = pd.DatetimeIndex(spy['Date'])

    pnl_qty2 = simulate_equity(picks, '2')
    pnl_qty1 = simulate_equity(picks, '1')
    pnl_sixt = simulate_equity(picks, 'kelly_0.0625')
    eq_qty2 = START_BANKROLL + pnl_qty2.reindex(td, fill_value=0.0).cumsum()
    eq_qty1 = START_BANKROLL + pnl_qty1.reindex(td, fill_value=0.0).cumsum()
    eq_sixt = START_BANKROLL + pnl_sixt.reindex(td, fill_value=0.0).cumsum()
    spy_eq  = START_BANKROLL * (spy['Close'].values / spy['Close'].iloc[0])

    n_years = (td[-1] - td[0]).days / 365.25
    def max_dd(eq):
        peak = eq.cummax()
        return float(((eq-peak)/peak).min())
    def summary_for(eq, name):
        eq_s = eq if isinstance(eq, pd.Series) and isinstance(eq.index, pd.DatetimeIndex) \
               else pd.Series(eq, index=td)
        ret = eq_s.diff().fillna(0) / eq_s.shift(1).fillna(START_BANKROLL)
        sd = ret.std(ddof=0)
        sh = float(ret.mean()*np.sqrt(252)/sd) if sd > 0 else 0.0
        weekly = eq_s.resample('W-FRI').last().ffill().pct_change().dropna()
        wsd = weekly.std(ddof=0)
        wsh = float(weekly.mean()*np.sqrt(52)/wsd) if wsd > 0 else 0.0
        final = float(eq_s.iloc[-1])
        cagr = ((final/START_BANKROLL)**(1/n_years) - 1) if final > 0 else -1
        return {
            f'{name}_final':         round(final, 2),
            f'{name}_total_return':  round((final - START_BANKROLL)/START_BANKROLL*100, 2),
            f'{name}_cagr':          round(cagr*100, 2),
            f'{name}_sharpe':        round(sh, 2),
            f'{name}_sharpe_weekly': round(wsh, 2),
            f'{name}_max_dd':        round(max_dd(eq_s)*100, 2),
        }

    summary = {
        'window_start': td[0].strftime('%Y-%m-%d'),
        'window_end':   td[-1].strftime('%Y-%m-%d'),
        'years':        round(n_years, 2),
        'trading_days': len(td),
        'n_trades':     int(len(picks)),
    }
    summary.update(summary_for(eq_qty2, 'strategy'))
    summary.update(summary_for(eq_qty1, 'qty1'))
    summary.update(summary_for(eq_sixt, 'sixteenk'))
    summary.update(summary_for(pd.Series(spy_eq, index=td), 'spy'))

    ml_col = picks['max_loss_dollar']
    def _kelly_qty(r, frac=0.0625):
        ws = r.get('w_star'); mld = r['max_loss_dollar']
        if ws is None or pd.isna(ws) or ws <= 0 or mld <= 0: return 1
        return max(1, min(5, int(frac*float(ws)*START_BANKROLL/mld)))
    wag2 = float((ml_col*2).sum()); wag1 = float(ml_col.sum())
    wagk = float((picks.apply(_kelly_qty, axis=1)*ml_col).sum())
    summary['strategy_wagered'] = round(wag2, 2)
    summary['qty1_wagered']     = round(wag1, 2)
    summary['sixteenk_wagered'] = round(wagk, 2)
    summary['strategy_yield'] = round(100*(summary['strategy_final']-START_BANKROLL)/wag2, 2) if wag2 > 0 else 0
    summary['qty1_yield']     = round(100*(summary['qty1_final']-START_BANKROLL)/wag1, 2) if wag1 > 0 else 0
    summary['sixteenk_yield'] = round(100*(summary['sixteenk_final']-START_BANKROLL)/wagk, 2) if wagk > 0 else 0

    points = [{'date': d.strftime('%Y-%m-%d'),
               'strategy': round(float(eq_qty2.iloc[i]), 2),
               'qty1':     round(float(eq_qty1.iloc[i]), 2),
               'sixteenk': round(float(eq_sixt.iloc[i]), 2),
               'spy':      round(float(spy_eq[i]), 2)} for i, d in enumerate(td)]

    sp = picks.sort_values(['entry_date_dt','GROUND'], ascending=[True,False]).copy()
    sp['_q'] = 2
    sp['_pnl'] = sp['_q'] * sp['pnl_per_contract']
    sp['_week'] = sp['entry_date_dt'].dt.to_period('W-FRI')
    running = START_BANKROLL
    sp['_pre_bank'] = 0.0; sp['_post_bank'] = 0.0
    for idx in sp.index:
        sp.at[idx, '_pre_bank'] = running
        running += sp.at[idx, '_pnl']
        sp.at[idx, '_post_bank'] = running

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
            'outcome':  r['_outcome'],
        }

    weeks = []
    for wk, grp in sp.groupby('_week', sort=True):
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
            'credit':      round(float((grp['credit']*grp['_q']).sum()*100), 2),
            'risk':        round(float((grp['max_loss_adj']*grp['_q']).sum()*100), 2),
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
            'selection':  f'top-5 per day, k={K_VAL:g}, GROUND threshold {THR:g} (all days)',
            'scoring':    'G_rv: RV-implied N(d2) probs in G; rv_vs_iv DKL (BS d2, 10d RV vs IV); raw combo MID credit (canon 2026-06-10)',
            'fill_basis': f'{FILL_FRAC:.2f}\u00d7mid (real fills ~0.82\u00d7mid, n=5); partial-WIN at 50% intrinsic',
            'regime':     'OFF (both directions eligible)',
            'vol_gate':   'OFF',
            'sizing':     'qty=2 per pick (canonical 2026-06-05)',
            'starting_bankroll': START_BANKROLL,
        },
        'summary': summary,
        'points':  points,
        'trades':  trade_log_rows,
        'weeks':   weeks,
    }
    print(f'{label}: n={len(picks)}  qty1 ${summary["qty1_final"]:,.0f} ({summary["qty1_total_return"]:+.1f}%, '
          f'Sh(wk) {summary["qty1_sharpe_weekly"]:+.2f}, DD {summary["qty1_max_dd"]:.1f}%)  '
          f'qty2 ${summary["strategy_final"]:,.0f} ({summary["strategy_total_return"]:+.1f}%, '
          f'Sh(wk) {summary["strategy_sharpe_weekly"]:+.2f}, DD {summary["strategy_max_dd"]:.1f}%)')
    return payload


if __name__ == '__main__':
    bt = select_picks('output/sweep_midmkt_v2_2020_25.parquet')
    payload = build_payload(bt, 2025, 'backtest 2020-25')
    with open('live/data/backtest_equity.json', 'w') as f:
        json.dump(payload, f, indent=2)
    print('Wrote live/data/backtest_equity.json')

    oot = select_picks('output/sweep_midmkt_v2_oot2026.parquet')
    payload = build_payload(oot, 2026, 'OOT 2026')
    with open('live/data/oot_equity.json', 'w') as f:
        json.dump(payload, f, indent=2)
    print('Wrote live/data/oot_equity.json')
