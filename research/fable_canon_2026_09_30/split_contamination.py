"""How much of the site book is contaminated by (a) unadjusted corporate actions in output/daily_closes.parquet
feeding the DEMEANED P_real, and (b) a smile-fit model credit far above the day's quoted mid.
Metrics mirror report_mid_canon.build_payload. Nothing is written to live/data."""
import sys, warnings; warnings.filterwarnings("ignore")
from pathlib import Path
import numpy as np, pandas as pd
ROOT = Path(__file__).resolve().parents[2]; sys.path.insert(0, str(ROOT))
import ent_canon as ec, report_mid_canon as rmc
HERE = Path(__file__).resolve().parent; BANK = rmc.START_BANKROLL
SPY = pd.read_csv(ROOT / "data/spy_us_d.csv", parse_dates=["Date"]).sort_values("Date")
KEY = ["ticker", "entry_date", "expiry_date", "spread_type", "short_strike", "long_strike"]
P = pd.read_parquet(HERE / "picks_C_252_k24.parquet")      # the DEPLOYED book (1.00x model fills)
q = pd.read_parquet(HERE / "frame_quotes_oi1.parquet")[KEY + ["vendor_mid", "s_bid", "s_ask", "l_bid", "l_ask"]]
P = P.merge(q, on=KEY, how="left")
print(f"deployed book: {len(P)} picks, credit/model median {(P.credit/P.model_credit).median():.3f}")

# (a) unadjusted corporate actions: any |1-session return| > 25% in the name's close history
cl = ec.backtest_closes().dropna().drop_duplicates(["ticker", "date"]).sort_values(["ticker", "date"])
events = {}
for tk, g in cl.groupby("ticker"):
    c = g.close.values.astype(float); d = pd.to_datetime(g.date).values
    r = c[1:] / c[:-1] - 1.0; j = np.where(np.abs(r) > 0.25)[0]
    if len(j): events[tk] = [(pd.Timestamp(d[i + 1]), float(r[i])) for i in j]
print(f"\n(a) names with a >25% single-session move in output/daily_closes.parquet: {len(events)}")
for tk, ev in sorted(events.items()):
    print("    " + tk + ": " + ", ".join(f"{t.date()} {100*v:+.0f}%" for t, v in ev))
def contaminated(row):
    for t, _ in events.get(row.ticker, []):
        # the P_real window is the 252 sessions of DTE-day moves ending the session before entry
        if row.entry_date - pd.Timedelta(days=380) <= t <= row.entry_date:
            return True
    return False
P["split_win"] = P.apply(contaminated, axis=1)
# (b) model credit far above the day's quoted mid, or a broken book
P["mdl_over_mid"] = P.model_credit / P.vendor_mid
P["ba_pct"] = (P.s_ask - P.s_bid) / ((P.s_ask + P.s_bid) / 2)
P["bad_quote"] = (P.vendor_mid <= 0) | (P.mdl_over_mid > 1.25) | (P.ba_pct > 0.5)
P["bps"] = 1e4 * P.GROUND
for lab, m in (("split window", P.split_win), ("model > 1.25x quoted mid / broken book", P.bad_quote),
               ("GROUND > 50 bps", P.bps > 50), ("GROUND > 100 bps", P.bps > 100), ("either defect", P.split_win | P.bad_quote)):
    x = P[m]; print(f"\n    {lab}: {len(x)} picks ({100*len(x)/len(P):.1f}%), qty1 P&L ${x.pnl_per_contract.sum():,.0f} "
                    f"of ${P.pnl_per_contract.sum():,.0f} ({100*x.pnl_per_contract.sum()/P.pnl_per_contract.sum():.1f}%), win {100*x._outcome.eq('WIN').mean():.0f}%")

def metrics(picks, qty, end_year):
    start = picks.entry_date_dt.min().normalize()
    end = max(pd.Timestamp(f"{end_year}-12-31"), pd.to_datetime(picks.realize_date).max().normalize())
    td = pd.DatetimeIndex(SPY[(SPY.Date >= start) & (SPY.Date <= end)].Date)
    pnl = pd.Series((qty * picks.pnl_per_contract).values, index=pd.to_datetime(picks.realize_date)).groupby(level=0).sum()
    eq = BANK + pnl.reindex(td, fill_value=0.0).cumsum(); w = eq.diff().fillna(0).resample("W-FRI").sum()
    return dict(n=len(picks), final=round(float(eq.iloc[-1])), dsh=round(float(w.mean()*np.sqrt(52)/w.std(ddof=0)), 2),
                dd=round(100*float(((eq-eq.cummax())/eq.cummax()).min()), 1))
print("\n(c) BOOK WITH THE DEFECTIVE PICKS REMOVED (qty 2, the site headline; $10k start, 1.00x model)")
hdr = f"{'scenario':<42} | {'IS n':>5} {'final':>9} {'$-Sh':>5} {'DD':>7} | {'OOT n':>5} {'final':>9} {'$-Sh':>5} {'DD':>7}"
print(hdr); print("-" * len(hdr))
scen = [("as deployed", pd.Series(True, index=P.index)), ("drop split-window picks", ~P.split_win),
        ("drop model > 1.25x mid / broken", ~P.bad_quote), ("drop both defects", ~(P.split_win | P.bad_quote)),
        ("drop GROUND > 50 bps", P.bps <= 50), ("drop GROUND > 100 bps", P.bps <= 100),
        ("drop both defects AND > 50 bps", ~(P.split_win | P.bad_quote) & (P.bps <= 50))]
for lab, keep in scen:
    x = P[keep]; a = metrics(x[x.entry_date.dt.year <= 2025], 2, 2025); b = metrics(x[x.entry_date.dt.year == 2026], 2, 2026)
    print(f"{lab:<42} | {a['n']:>5} ${a['final']:>8,} {a['dsh']:>5} {a['dd']:>6}% | {b['n']:>5} ${b['final']:>8,} {b['dsh']:>5} {b['dd']:>6}%")
P[["ticker","entry_date","spread_type","short_strike","long_strike","model_credit","vendor_mid","mdl_over_mid","ba_pct","bps","split_win","bad_quote","pnl_per_contract","_outcome"]].to_csv(HERE / "contamination_flags.csv", index=False)
