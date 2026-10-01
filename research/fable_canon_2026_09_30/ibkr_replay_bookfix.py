"""IBKR fair replay with the credible-book quote gate (mini, 2026-10-01).

The replay in README_ibkr_replay.md gated on quoted mid >= model with no check on the book behind
the mid. Live now also requires both legs bid and (short ask-bid)+(long ask-bid) <= QUOTE_MAX_BOOK_W
x spread width (live/ranker.py). This joins each replay candidate to its two legs in the archived
snapshot, applies that rule, and reruns the same selection: model credit >= 0.45 x width, pooled
top 6 per scan by GROUND at k=24, 15:30 then 15:45 top-up to 6.
Writes ibkr_replay_candidates_1530_1545_book.csv and ibkr_replay_bookfix_picks.csv.
"""
import sys, warnings; warnings.filterwarnings("ignore")
from pathlib import Path
import numpy as np, pandas as pd
ROOT = Path(__file__).resolve().parents[2]; sys.path.insert(0, str(ROOT))
import config as cfg
from spreads import settle_pnl
HERE = Path(__file__).resolve().parent
W = float(getattr(cfg, "QUOTE_MAX_BOOK_W", 1.0)); FLOOR = 0.45; TOP = 6

R = pd.read_csv(HERE / "ibkr_replay_candidates_1530_1545.csv", dtype={"hm": str})
legs = []
for (day, hm), _ in R.groupby(["day", "hm"]):
    s = pd.read_parquet(ROOT / f"live/snapshots/{day}/{hm}.parquet",
                        columns=["Symbol", "ExpirationDate", "StrikePrice", "PutCall", "BidPrice", "AskPrice"])
    s["day"] = day; s["hm"] = hm; s["exp"] = pd.to_datetime(s.ExpirationDate).dt.strftime("%Y-%m-%d"); legs.append(s)
L = pd.concat(legs).drop_duplicates(["day", "hm", "Symbol", "exp", "StrikePrice", "PutCall"])
R["PutCall"] = np.where(R.spread_type.eq("bull_put"), "put", "call")
for leg in ("short", "long"):
    m = L.rename(columns={"Symbol": "ticker", "StrikePrice": f"{leg}_strike", "BidPrice": f"{leg}_bid", "AskPrice": f"{leg}_ask"})
    R = R.merge(m[["day", "hm", "ticker", "exp", f"{leg}_strike", "PutCall", f"{leg}_bid", f"{leg}_ask"]],
                on=["day", "hm", "ticker", "exp", f"{leg}_strike", "PutCall"], how="left")
R["leg_mid"] = (R.short_bid + R.short_ask) / 2 - (R.long_bid + R.long_ask) / 2
R["book"] = (R.short_ask - R.short_bid) + (R.long_ask - R.long_bid)
R["book_w"] = R.book / R.width
R["quote_ok"] = (R.short_bid > 0) & (R.long_bid > 0) & (R.book <= W * R.width)
print(f"candidates {len(R)}, legs found {int(R.short_bid.notna().sum())}/{int(R.long_bid.notna().sum())}, "
      f"leg mid == archived quote (1c) {int((abs(R.leg_mid - R.net_credit) <= 0.011).sum())}")
R.to_csv(HERE / "ibkr_replay_candidates_1530_1545_book.csv", index=False)

R = R[R.settle.notna() & (R.model_credit > 0) & R.net_credit.notna() & R.p.notna()].copy()
R["slot"] = R.hm.map(lambda h: "15:30" if h in ("1530", "1531") else "15:45")
key = ["day", "ticker", "short_strike", "long_strike", "exp"]

def select(E):
    E = E.copy(); E["r"] = E.groupby(["day", "hm"]).G24.rank(ascending=False, method="first"); T = E[E.r <= TOP]; out = []
    for day, g in T.groupby("day"):
        ga = g[g.slot == "15:30"]; gb = g[g.slot == "15:45"]; have = set(map(tuple, ga[key].values))
        add = gb[np.array([tuple(r) not in have for r in gb[key].values], dtype=bool)]
        out.append(pd.concat([ga, add.head(max(0, TOP - len(ga)))]))
    return pd.concat(out).reset_index(drop=True)

def stats(P, fill):
    c = (fill * P.model_credit).clip(upper=P.width - 0.01).round(4)
    pnl = np.array([settle_pnl(s, ks, kl, x, w - x, t) * 100 for s, ks, kl, x, w, t in
                    zip(P.settle, P.short_strike, P.long_strike, c, P.width, P.spread_type)])
    wk = pd.to_datetime(P.exp).dt.strftime("%m-%d"); byw = pd.Series(pnl).groupby(wk.values).sum()
    eq = byw.cumsum(); dd = (eq - eq.cummax().clip(lower=0)).min()
    return pnl, byw, dict(pnl=round(pnl.sum()), weeks_up=f"{int((byw > 0).sum())}/{len(byw)}", worst_wk=round(byw.min()), dd=round(dd),
                          risk=round(((P.width - c) * 100).sum()))

base = R[(R.model_cw >= FLOOR) & (R.quote_over_model >= 1.0)]
arms = {"old gate": base, "book fix": base[base.quote_ok]}
print(f"clear floor+quote: {len(base)}; of those on a wide/unbid book: {int((~base.quote_ok).sum())}")
res = {}
for name, E in arms.items():
    P = select(E); bp = P.spread_type.eq("bull_put")
    win = np.where(bp, P.settle >= P.short_strike, P.settle <= P.short_strike); loss = np.where(bp, P.settle <= P.long_strike, P.settle >= P.long_strike)
    p104, w104, s104 = stats(P, 1.04); p100, w100, s100 = stats(P, 1.00)
    P["pnl_104"] = p104; P["arm"] = name; res[name] = (P, w104)
    print(f"\n== {name}: {len(P)} picks, {P.day.nunique()} days, win {win.mean():.0%} loss {loss.mean():.0%} between {1 - win.mean() - loss.mean():.0%}")
    print(f"   1.04x model: {s104}"); print(f"   1.00x model: {s100}")
    print(f"   avg book/width {P.book_w.mean():.2f}, picks on a wide/unbid book {int((~P.quote_ok).sum())}")
print("\nweekly P&L at 1.04x (by expiry):"); print(pd.DataFrame({k: v[1] for k, v in res.items()}).fillna(0).round(0).astype(int).to_string())
a, b = res["old gate"][0], res["book fix"][0]
ka = set(map(tuple, a[key].values)); kb = set(map(tuple, b[key].values))
print(f"\nsame picks {len(ka & kb)}, dropped {len(ka - kb)}, added {len(kb - ka)}")
d = a[[tuple(r) not in kb for r in a[key].values]]; n = b[[tuple(r) not in ka for r in b[key].values]]
print(f"dropped picks P&L {d.pnl_104.sum():.0f}; added picks P&L {n.pnl_104.sum():.0f}")
print(d[["day", "hm", "ticker", "short_strike", "long_strike", "net_credit", "model_credit", "short_bid", "short_ask", "long_bid", "long_ask", "book_w", "pnl_104"]].round(2).to_string(index=False))
pd.concat([a, b]).to_csv(HERE / "ibkr_replay_bookfix_picks.csv", index=False)
