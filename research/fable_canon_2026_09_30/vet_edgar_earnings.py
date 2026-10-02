"""Vet SEC EDGAR Item 2.02 dates against the NASDAQ earnings history before merging (mini, 2026-10-01).

Rule, checked by reading the filings behind every doubtful date:
  * EDGAR date within 3 days of a NASDAQ date            -> same event, nothing to add.
  * unmatched, and NASDAQ has that quarter (a date within 45 days) whose own date is confirmed by
    another 8-K within 3 days                            -> EXTRA 2.02 filing, not earnings; dropped.
    (TSLA quarterly delivery reports, ABBV IPR&D pre-announcements, REGN January pre-announcements.)
  * unmatched, NASDAQ has NO date within 45 days         -> fills a hole; kept if it sits on the name's
    quarterly cadence. Manual exclusions below are filings read and found not to be earnings.
  * unmatched, NASDAQ's nearby date has no 8-K of its own -> date disagreement, settled by the filing.
Writes edgar_vetted_earnings_additions.csv (tracked) and output/earnings_history_merged.csv
(NASDAQ + additions - NASDAQ dates the filings contradict). Needs output/edgar_earnings_history.csv
and output/nasdaq_earnings_history.csv (run with --start 2020-01-01 --end 2026-12-31; it overwrites).
"""
import sys
from pathlib import Path
import numpy as np, pandas as pd
ROOT = Path(__file__).resolve().parents[2]; HERE = Path(__file__).resolve().parent
e = pd.read_csv(ROOT / "output/edgar_earnings_history.csv", parse_dates=["EarningsDate"])
n = pd.read_csv(ROOT / "output/nasdaq_earnings_history.csv", parse_dates=["EarningsDate"])
e = e[e.EarningsDate >= "2020-01-01"].copy()
# Filings read on EDGAR 2026-10-01 and found NOT to be an earnings release.
NOT_EARNINGS = {("HON", "2023-12-11"): "business-unit realignment",
                ("GE", "2020-04-09"): "expected Q1 performance / guidance withdrawal",
                ("GE", "2020-04-13"): "financing actions press release"}
# NASDAQ dates the company's own 8-K contradicts.
NASDAQ_WRONG = {("DE", "2026-02-12"): "8-K Item 2.02 for fiscal Q1 2026 is dated 2026-02-19"}
nd = {s: g.EarningsDate.values for s, g in n.groupby("Symbol")}; ed = {s: g.EarningsDate.values for s, g in e.groupby("Symbol")}
days = lambda a, d: np.abs((a - np.datetime64(d)).astype("timedelta64[D]").astype(int))
rows = []
for s, d in zip(e.Symbol, e.EarningsDate):
    a = nd.get(s, np.array([], dtype="datetime64[ns]")); key = (s, str(d.date()))
    if len(a) and (days(a, d) <= 3).any():
        continue
    if key in NOT_EARNINGS:
        rows.append((s, d, "dropped", NOT_EARNINGS[key])); continue
    if len(a) and (days(a, d) <= 45).any():
        nq = a[np.argmin(days(a, d))]
        if (days(ed[s], nq) <= 3).any():
            rows.append((s, d, "dropped", f"extra 2.02 filing; NASDAQ {pd.Timestamp(nq).date()} has its own 8-K")); continue
        rows.append((s, d, "added", f"replaces NASDAQ {pd.Timestamp(nq).date()} (no 8-K within 3 days of it)")); continue
    rows.append((s, d, "added", "NASDAQ has no date within 45 days"))
V = pd.DataFrame(rows, columns=["Symbol", "EarningsDate", "verdict", "why"])
add = V[V.verdict == "added"]; add.to_csv(HERE / "edgar_vetted_earnings_additions.csv", index=False)
drop = pd.DataFrame([(s, pd.Timestamp(d)) for s, d in NASDAQ_WRONG], columns=["Symbol", "EarningsDate"])
m = n.merge(drop.assign(_x=1), on=["Symbol", "EarningsDate"], how="left"); m = m[m._x.isna()][["Symbol", "EarningsDate"]]
m = pd.concat([m, add[["Symbol", "EarningsDate"]]]).drop_duplicates().sort_values(["EarningsDate", "Symbol"])
m.to_csv(ROOT / "output/earnings_history_merged.csv", index=False)
print(f"unmatched EDGAR dates {len(V)}: added {len(add)}, dropped {len(V) - len(add)}")
print("added by name:", add.groupby("Symbol").size().to_dict()); print("dropped by name:", V[V.verdict == 'dropped'].groupby("Symbol").size().sort_values(ascending=False).head(12).to_dict())
print(f"NASDAQ {len(n)} -> merged {len(m)} (removed {len(drop)} contradicted NASDAQ date(s))")
cnt = m[m.EarningsDate.between('2020-01-01', '2025-12-31')].assign(y=lambda x: x.EarningsDate.dt.year).groupby(["Symbol", "y"]).size()
print("name-years 2020-25 with more than 4 dates after merge:", cnt[cnt > 4].to_dict()); print("with fewer than 4:", int((cnt < 4).sum()))
