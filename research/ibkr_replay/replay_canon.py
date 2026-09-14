"""Replay the current D_ent canon over stored IBKR snapshots, and compare with
what the canon of the day actually picked.

Answers: "what would the new canon have done over this window?" using only data
we held live -- no vendor chains, no Yahoo, no output/daily_closes.parquet.

    chains  live/snapshots/<date>/<hhmm>.parquet, the exact file each freeze used
    closes  research/ibkr_replay/ibkr_closes.parquet (fetch_ibkr_closes.py)
    credit  leg mids from that same snapshot

Combo re-pricing is stubbed out on purpose. live/ranker._reprice_on_combos asks
IBKR for a combo quote NOW, which on a historical replay would price today's book
against August strikes. Leg mids from the snapshot are the honest stand-in and are
mildly conservative (a real combo fill usually beats them).

    python research/ibkr_replay/replay_canon.py [--since 2026-05-27] [--fill 1.08]

Coverage is bounded by live/snapshots/, NOT by live/frozen/. The History tab keeps
five picks per day; fit_smiles() needs the whole strike ladder, so a day without a
stored chain cannot be replayed at all. On the Mac mini that floor is 2026-08-20,
the cutover date -- earlier chains were written on the MacBook.
"""
from __future__ import annotations
import argparse, os, sys, warnings
from pathlib import Path

warnings.filterwarnings('ignore')
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import ent_canon as ec
import live.closes as LC
import live.ranker as R
from live.webapp import _frozen_history

CLOSES = ROOT / 'research' / 'ibkr_replay' / 'ibkr_closes.parquet'


def settle(st, ks, kl, width, credit, spot, haircut=True):
    """(outcome, pnl per contract after commission). Backtest basis: a winning
    PARTIAL is halved, a losing one is taken in full."""
    if spot is None or credit is None:
        return None, None
    ml = width - credit
    if ml <= 0:
        return None, None
    if st == 'bull_put':
        out = 'WIN' if spot > ks else ('LOSS' if spot <= kl else 'PARTIAL')
        v = credit if spot >= ks else (-ml if spot <= kl else credit - (ks - spot))
    else:
        out = 'WIN' if spot < ks else ('LOSS' if spot >= kl else 'PARTIAL')
        v = credit if spot <= ks else (-ml if spot >= kl else credit - (spot - ks))
    v *= 100
    if haircut and out == 'PARTIAL' and v > 0:
        v *= 0.5
    return out, v - ec.COMMISSION


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--since', default='2026-05-27')
    ap.add_argument('--fill', type=float, default=ec.FILL_MULT,
                    help='multiple of model credit to book the new canon at')
    ap.add_argument('--closes', default=str(CLOSES))
    ap.add_argument('--outdir', default=str(ROOT / 'research' / 'ibkr_replay'))
    a = ap.parse_args()

    if not os.path.exists(a.closes):
        sys.exit(f"missing {a.closes}\n  run research/ibkr_replay/fetch_ibkr_closes.py first")
    ibc = pd.read_parquet(a.closes)
    ibc['date'] = pd.to_datetime(ibc['date']).dt.normalize()

    # IBKR-only: the ranker must not see the vendor-seeded store.
    R._reprice_on_combos = lambda c: c
    LC.load_closes = lambda: ibc[['ticker', 'date', 'close']].copy()
    R.load_closes = LC.load_closes

    px = {(r.ticker, r.date.strftime('%Y-%m-%d')): r.close for r in ibc.itertuples()}
    days = [d for d in _frozen_history(limit=1000) if d['date'] >= a.since]
    print(f"{len(days)} frozen days since {a.since}; replaying those with a stored chain\n")

    new, old, skipped = [], [], []
    for d in sorted(days, key=lambda x: x['date']):
        day, sf = d['date'], d.get('snapshot_file')
        if not sf or not os.path.exists(sf):
            skipped.append(day); continue
        try:
            rk = R.rank_snapshot(pd.read_parquet(sf))
        except Exception as e:
            skipped.append(f"{day} (rank failed: {e})"); continue
        if rk.empty:
            skipped.append(f"{day} (no ranked rows)"); continue
        q = rk[rk.qualified == True].sort_values('GROUND', ascending=False).head(ec.TOP_N)
        for _, r in q.iterrows():
            exp = pd.Timestamp(r['expiry_date']).strftime('%Y-%m-%d')
            c = round(float(r['model_credit']) * a.fill, 4)
            o, p = settle(r['spread_type'], float(r['short_strike']), float(r['long_strike']),
                          float(r['width']), c, px.get((r['ticker'], exp)))
            new.append(dict(day=day, ticker=r['ticker'], st=r['spread_type'], exp=exp,
                            ks=r['short_strike'], kl=r['long_strike'], w=r['width'],
                            model=r['model_credit'], credit=c, quoted=r['net_credit'],
                            delta=abs(r.get('dfit_short', float('nan'))),
                            ground=r['GROUND'], out=o, pnl=p))
        for p_ in (d.get('top_picks') or []):
            ft = (p_.get('fill_targets') or [{}])[0]
            exp = str(p_['expiry_date'])[:10]
            o, pl = settle(p_['spread_type'], p_['short_strike'], p_['long_strike'],
                           p_['spread_width'], ft.get('credit') or p_.get('net_credit'),
                           px.get((p_['ticker'], exp)))
            old.append(dict(day=day, ticker=p_['ticker'], st=p_['spread_type'], exp=exp,
                            credit=ft.get('credit'), delta=abs(p_.get('short_delta') or 0),
                            out=o, pnl=pl))
        print(f"  {day}: new {len(q)} qualified | old {len(d.get('top_picks') or [])}", flush=True)

    N, O = pd.DataFrame(new), pd.DataFrame(old)
    os.makedirs(a.outdir, exist_ok=True)
    N.to_pickle(os.path.join(a.outdir, 'replay_new.pkl'))
    O.to_pickle(os.path.join(a.outdir, 'replay_old.pkl'))

    print(f"\nskipped {len(skipped)} day(s) with no stored chain")
    if skipped:
        print("   ", skipped[:10], "..." if len(skipped) > 10 else "")
    hdr = f"{'book':<26}{'n':>5}{'med c/w':>9}{'win%':>7}{'avgW':>9}{'avgL':>9}{'payoff':>8}{'total':>10}"
    print("\n" + hdr)
    for df, lbl in ((N, f'NEW canon @{a.fill:g}x model'), (O, 'OLD canon (as booked)')):
        s = df.dropna(subset=['pnl'])
        if s.empty:
            print(f"{lbl:<26}  no settled trades"); continue
        w, l = s[s.out == 'WIN'], s[s.out == 'LOSS']
        cw = (s.credit / s.w).median() if 'w' in s else float('nan')
        po = w.pnl.mean() / abs(l.pnl.mean()) if len(l) else float('nan')
        print(f"{lbl:<26}{len(s):>5}{cw:>9.3f}{100*len(w)/len(s):>6.1f}%"
              f"{w.pnl.mean():>9.2f}{l.pnl.mean():>9.2f}{po:>8.3f}{s.pnl.sum():>10,.0f}")
    print("\nwrote replay_new.pkl / replay_old.pkl to", a.outdir)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
