"""Regenerate live/data/backtest_equity.json + oot_equity.json under the D_ent canon (2026-09-13).

Selection and pricing come from the research frame research/dkl_2026_09_13/featATM6.parquet, which
holds every 50-60 delta candidate 2020-2026 with: fitted-delta strikes, smile-fit model credit,
P_real (p, q, ro), EV, D_ent and the realized outcome. That frame is rebuilt by
research/dkl_2026_09_13/{band_sweep,band_checks,atm_dkl,atm2,..}.py from the vendor year files.

Payload shape is report_mid_canon.build_payload's (summary / points / weeks / trades, qty1 /
strategy / sixteenk / spy), so the webapp renders it unchanged.
"""
import json, sys, os
import numpy as np, pandas as pd
sys.path.insert(0, '.')
import ent_canon as ec
import report_mid_canon as rmc

FRAME = 'research/dkl_2026_09_13/featATM6.parquet'
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


def select(win):
    C = pd.read_parquet(FRAME)
    C = C[(C.win == win)].dropna(subset=['EV']).copy()
    C['entry_date'] = pd.to_datetime(C.entry_date); C['expiry_date'] = pd.to_datetime(C.expiry_date)
    C['GROUND'] = C.EV * np.exp(-ec.K * C.D_ent)
    sel = (C[C.GROUND >= ec.THR].sort_values(['entry_date', 'GROUND'], ascending=[True, False])
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
    c['selection'] = ec.CANON_LABELS['selection'] + (' — frozen canon, no 2026 tuning' if oot else ' (all days)')
    c['scoring'] = ec.CANON_LABELS['scoring']
    c['fill_basis'] = ec.CANON_LABELS['fill'] + '; partial-WIN at 50% intrinsic'
    c['targets'] = ec.CANON_LABELS['targets']
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
