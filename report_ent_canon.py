"""Regenerate live/data/backtest_equity.json + oot_equity.json under the D_ent canon.

Candidates and pricing come from the research frame research/dkl_2026_09_13/featATM8.parquet, which
holds every 50-60 delta candidate 2020-2026 with: fitted-delta strikes, smile-fit model credit,
D_ent and the realized outcome. P_real (p, q, ro) and EV are RECOMPUTED here on the full-session
close series (2026-09-15); the frame's own p/q/ro were built on a sparse candidate-day series. That frame is rebuilt by
research/dkl_2026_09_13/{band_sweep,band_checks,atm_dkl,atm2,..}.py from the vendor year files.

Payload shape is report_mid_canon.build_payload's (summary / points / weeks / trades, qty1 /
strategy / sixteenk / spy), so the webapp renders it unchanged.
"""
import json, sys, os
import numpy as np, pandas as pd
sys.path.insert(0, '.')
import ent_canon as ec
import report_mid_canon as rmc

FRAME = 'research/dkl_2026_09_13/featATM8.parquet'
GAP_SERIES = 'output/name_gaps_backtest.parquet'   # build_name_gaps.py (per-name own gaps)
CHAIN_FEATURES = 'research/option_direction_2026_09_16/chain_direction_features.parquet'
SPY_CSV = 'data/spy_us_d.csv'
DOW = {0: 'Mon', 1: 'Tue', 2: 'Wed', 3: 'Thu', 4: 'Fri'}


def _outcome(r):
    sp, ss, ls = r['expiry_close'], r['short_strike'], r['long_strike']
    if r['spread_type'] == 'bull_put':
        return 'WIN' if sp > ss else ('LOSS' if sp <= ls else 'PARTIAL')
    return 'WIN' if sp < ss else ('LOSS' if sp >= ls else 'PARTIAL')


def _pnl(r):
    c, ml, sp, ss, ls = r['credit'], r['max_loss_adj'], r['expiry_close'], r['short_strike'], r['long_strike']
    if r['spread_type'] == 'bull_put':
        pnl = c if sp >= ss else (-ml if sp <= ls else c - (ss - sp))
    else:
        pnl = c if sp <= ss else (-ml if sp >= ls else c - (sp - ss))
    pnl *= 100
    if r['_outcome'] == 'PARTIAL' and pnl > 0:
        pnl *= 0.5
    return pnl - ec.COMMISSION


def _prior_spy_bull(entry_dates):
    """Classify each entry using only the prior completed SPY session."""
    spy = pd.read_csv(SPY_CSV, parse_dates=['Date']).sort_values('Date')
    spy['Date'] = spy['Date'].dt.normalize()
    spy['SMA'] = spy['Close'].rolling(100, min_periods=100).mean()
    spy = spy.dropna(subset=['SMA'])
    regime = pd.Series(np.where(spy['Close'] > spy['SMA'], 'bull', 'bear'),
                       index=pd.DatetimeIndex(spy['Date'])).sort_index()
    out = []
    for dt in pd.DatetimeIndex(entry_dates).normalize():
        pos = regime.index.searchsorted(dt, side='left') - 1
        out.append(regime.iloc[pos] if pos >= 0 else None)
    return out


def select(win):
    C = pd.read_parquet(FRAME)
    C = C[(C.win == win)].dropna(subset=['EV']).copy()
    C['entry_date'] = pd.to_datetime(C.entry_date); C['expiry_date'] = pd.to_datetime(C.expiry_date)
    # 2026-09-15: P_real recomputed on the FULL-SESSION close series (ec.backtest_closes), not the frame's
    # sparse candidate-day series (20-35% of sessions missing, handoff §0.42). Frame p/q/ro/EV are overwritten.
    CLS = ec.backtest_closes()
    # 2026-09-16 (§0.45): own-gap drift shifts P_real. GAP_GAMMA=0 reproduces the pre-gap canon.
    MU = ec.gap_drift(C, CLS, pd.read_parquet(GAP_SERIES)) if ec.GAP_GAMMA else None
    P = ec.p_real(C, CLS, mu=MU)
    C['p'], C['q'], C['ro'] = P[:, 0], P[:, 1], P[:, 2]
    b = C.model_credit.values / (C.width.values - C.model_credit.values)
    _, ell = ec.kelly(np.nan_to_num(C.p.values), np.nan_to_num(C.q.values), np.nan_to_num(C.ro.values), b)
    ell[~np.isfinite(C.p.values)] = np.nan
    C['EV'] = np.exp(ell) - 1.0
    C = C.dropna(subset=['EV']).copy()
    C['GROUND'] = C.EV * np.exp(-ec.K * C.D_ent)

    # Same-strike option parity is ranked across all bull-put candidates on
    # each entry date before the GROUND and regime gates are applied.
    F = pd.read_parquet(CHAIN_FEATURES, columns=['ticker', 'entry_date', 'expiry_date', 'cp_iv_gap'])
    F['entry_date'] = pd.to_datetime(F['entry_date']).dt.normalize()
    F['expiry_date'] = pd.to_datetime(F['expiry_date']).dt.normalize()
    C = C.merge(F, on=['ticker', 'entry_date', 'expiry_date'], how='left')
    C['parity_bull_raw'] = -C['cp_iv_gap']
    C = ec.add_parity_percentile(C)
    C['spy_regime_prior'] = _prior_spy_bull(C['entry_date'])

    pool = C[
        C['spread_type'].eq('bull_put')
        & C['spy_regime_prior'].eq('bull')
        & (C['parity_pct'] > ec.PARITY_MIN_PCT)
        & (C['GROUND'] >= ec.THR)
    ]
    sel = (pool.sort_values(['entry_date', 'GROUND'], ascending=[True, False])
           .groupby('entry_date').head(ec.TOP_N)).copy()
    sel['credit'] = (sel.model_credit * ec.FILL_MULT).round(4)
    sel['max_loss_adj'] = (sel.width - sel.credit).round(4)
    sel = sel[sel.max_loss_adj > 0].copy()
    sel['_outcome'] = sel.apply(_outcome, axis=1)
    sel['pnl_per_contract'] = sel.apply(_pnl, axis=1)
    sel['max_loss_dollar'] = sel.max_loss_adj * 100
    w, ell = ec.kelly(sel.p.values, sel.q.values, sel.ro.values, sel.model_credit.values / (sel.width.values - sel.model_credit.values))
    sel['w_star'] = w; sel['G'] = ell; sel['DKL'] = sel.D_ent
    sel['realize_date'] = sel.expiry_date; sel['entry_date_dt'] = sel.entry_date
    sel['short_delta'] = np.where(sel.spread_type == 'bull_put', -sel.dfit_short, sel.dfit_short)
    return sel.sort_values('entry_date_dt').reset_index(drop=True)


def patch_config(payload, oot=False):
    c = payload['config']
    c['delta'] = ec.CANON_LABELS['delta']; c['dkl'] = ec.CANON_LABELS['dkl']; c['window'] = ec.CANON_LABELS['window']
    c['selection'] = ec.CANON_LABELS['selection'] + (' — configuration selected with 2026 in view: NOT a clean holdout' if oot else ' (all days)')
    c['scoring'] = ec.CANON_LABELS['scoring']
    c['gap_drift'] = ec.CANON_LABELS['gap']
    c['fill_basis'] = ec.CANON_LABELS['fill'] + '; partial-WIN at 50% intrinsic'
    c['targets'] = ec.CANON_LABELS['targets']
    c['regime'] = 'bull puts only when prior-session SPY close > 100d SMA; cash otherwise'
    c['parity'] = f'same-strike call-IV minus put-IV daily percentile > {ec.PARITY_MIN_PCT:.0%}'
    c['credit_basis'] = 'smile-fit model credit (no vendor quote at the strike is used)'
    return payload


if __name__ == '__main__':
    for win, end_year, label, out in (('IS', 2025, 'backtest 2020-25', 'live/data/backtest_equity.json'),
                                      ('OOT', 2026, 'OOT 2026', 'live/data/oot_equity.json')):
        picks = select(win)
        payload = patch_config(rmc.build_payload(picks, end_year, label), oot=(win == 'OOT'))
        # trade rows: add the model credit + target range so the tables can show them
        by_key = {(r.ticker, r.entry_date_dt.strftime('%Y-%m-%d'), r.short_strike): r for r in picks.itertuples()}
        for t in payload['trades']:
            r = by_key.get((t['ticker'], t['entry'], t['k_s']))
            if r is not None:
                tg = ec.credit_targets(r.dfit_short, r.DTE, width=r.width, model_credit=r.model_credit)
                t['model_credit'] = round(float(r.model_credit), 4); t['dte'] = int(r.DTE)
                t['short_delta'] = round(float(r.dfit_short), 3); t['min_credit'] = tg['min_credit']
                t['target_lo'] = tg['target_lo_credit']; t['target_hi'] = tg['target_hi_credit']
        for w_ in payload['weeks']:
            for t in w_['trades']:
                r = by_key.get((t['ticker'], t['entry'], t['k_s']))
                if r is not None:
                    tg = ec.credit_targets(r.dfit_short, r.DTE, width=r.width, model_credit=r.model_credit)
                    t['model_credit'] = round(float(r.model_credit), 4); t['dte'] = int(r.DTE)
                    t['short_delta'] = round(float(r.dfit_short), 3); t['min_credit'] = tg['min_credit']
                    t['target_lo'] = tg['target_lo_credit']; t['target_hi'] = tg['target_hi_credit']
        with open(out, 'w') as f:
            json.dump(payload, f, indent=2)
        print('Wrote', out)
