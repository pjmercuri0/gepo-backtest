"""Compare two scored-candidate snapshots and report drift on the spreads
that appear in both.

Inputs: two parquet files produced by analyze_chain.py. Typically used like:

   # 3:30 PM signal snapshot vs 4:00 PM close
   python3 drift_compare.py \\
     --before live/data/scored/2026-05-20/1530.parquet \\
     --after  live/data/scored/2026-05-20/1600.parquet

   # Yesterday EOD vs today EOD (carry drift on a held position)
   python3 drift_compare.py \\
     --before live/data/scored/2026-05-19/1600.parquet \\
     --after  live/data/scored/2026-05-20/1600.parquet

Outputs:
   live/data/drift/{before_date}_{before_time}__{after_date}_{after_time}.csv
   live/data/drift/{before_date}_{before_time}__{after_date}_{after_time}.html

Join key: (ticker, spread_type, expiry_date, short_strike, long_strike).
Only spreads present in BOTH snapshots are included in drift.

Drift columns:
   d_mid_credit    : after.mid_credit − before.mid_credit
   d_GROUND        : after.GROUND − before.GROUND   (raw Γᵢ change)
   d_GROUND_pct    : relative change in Γᵢ
   d_short_bid     : raw bid move on the short leg
   d_long_ask      : raw ask move on the long leg
   d_cross_bidask  : after.cross_bidask_credit − before.cross_bidask_credit
   d_underlying    : implied underlying move (recomputed)

This is the answer to "if I made my pick at 3:30 and traded at 4:00, what
moved against me." It's also the right tool for "where did the EOD quote
land vs the backtest's recorded entry."
"""
from __future__ import annotations
import argparse
import os
import sys

import numpy as np
import pandas as pd


KEY_COLS = ["ticker", "spread_type", "expiry_date", "short_strike", "long_strike"]


def parse_snap_id(path: str) -> tuple[str, str]:
    """Extract (date, time) from a path like 'live/data/scored/YYYY-MM-DD/HHMM.parquet'."""
    p = os.path.abspath(path)
    parts = p.split(os.sep)
    date_str = parts[-2]
    time_str = os.path.splitext(parts[-1])[0]
    return date_str, time_str


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--before", required=True, help="earlier scored parquet")
    ap.add_argument("--after",  required=True, help="later scored parquet")
    ap.add_argument("--output-root", default="live/data/drift")
    args = ap.parse_args()

    b_date, b_time = parse_snap_id(args.before)
    a_date, a_time = parse_snap_id(args.after)
    tag = f"{b_date}_{b_time}__{a_date}_{a_time}"

    print(f"\n== drift {tag} ==\n", flush=True)

    a = pd.read_parquet(args.before)
    b = pd.read_parquet(args.after)
    print(f"   before: {len(a):,} candidates ({b_date} {b_time})")
    print(f"   after : {len(b):,} candidates ({a_date} {a_time})")

    a = a.copy(); b = b.copy()
    a["expiry_date"] = pd.to_datetime(a["expiry_date"]).dt.date.astype(str)
    b["expiry_date"] = pd.to_datetime(b["expiry_date"]).dt.date.astype(str)

    keep_a = KEY_COLS + ["mid_credit", "cross_bidask_credit", "GROUND",
                          "G", "EV", "DKL", "short_bid", "short_ask",
                          "long_bid", "long_ask"]
    keep_b = keep_a

    a = a[keep_a].add_suffix("_a")
    b = b[keep_b].add_suffix("_b")
    # rename keys back
    for k in KEY_COLS:
        a = a.rename(columns={f"{k}_a": k})
        b = b.rename(columns={f"{k}_b": k})

    m = a.merge(b, on=KEY_COLS, how="inner")
    print(f"   matched: {len(m):,} spreads in both snapshots", flush=True)
    if m.empty:
        print("   nothing to compare; exiting")
        return 0

    m["d_mid_credit"]    = m["mid_credit_b"]          - m["mid_credit_a"]
    m["d_cross_bidask"]  = m["cross_bidask_credit_b"] - m["cross_bidask_credit_a"]
    m["d_short_bid"]     = m["short_bid_b"]           - m["short_bid_a"]
    m["d_long_ask"]      = m["long_ask_b"]            - m["long_ask_a"]
    m["d_GROUND"]        = m["GROUND_b"]              - m["GROUND_a"]
    m["d_GROUND_pct"]    = np.where(m["GROUND_a"].abs() > 1e-9,
                                     m["d_GROUND"] / m["GROUND_a"].abs() * 100,
                                     np.nan)
    m["d_G"]             = m["G_b"]                   - m["G_a"]
    m["d_DKL"]           = m["DKL_b"]                 - m["DKL_a"]

    out_dir = args.output_root
    os.makedirs(out_dir, exist_ok=True)
    out_csv  = os.path.join(out_dir, f"{tag}.csv")
    out_html = os.path.join(out_dir, f"{tag}.html")

    m.to_csv(out_csv, index=False)
    print(f"   wrote {out_csv}")

    # quick stdout report
    print(f"\n   d_mid_credit         : median {m['d_mid_credit'].median():+.4f}  "
          f"mean {m['d_mid_credit'].mean():+.4f}  std {m['d_mid_credit'].std():.4f}")
    print(f"   d_GROUND (Γᵢ)        : median {m['d_GROUND'].median()*100:+.3f}pp  "
          f"mean {m['d_GROUND'].mean()*100:+.3f}pp")
    print(f"   d_short_bid          : median {m['d_short_bid'].median():+.4f}  "
          f"mean {m['d_short_bid'].mean():+.4f}")
    print(f"   d_long_ask           : median {m['d_long_ask'].median():+.4f}  "
          f"mean {m['d_long_ask'].mean():+.4f}")
    print(f"   d_cross_bidask_credit: median {m['d_cross_bidask'].median():+.4f}  "
          f"mean {m['d_cross_bidask'].mean():+.4f}")

    # HTML
    html = render_html(m, tag)
    with open(out_html, "w") as f:
        f.write(html)
    print(f"   wrote {out_html}")


def render_html(m: pd.DataFrame, tag: str) -> str:
    # Top 10 spreads with the largest adverse drift (mid_credit dropped most)
    worst = m.sort_values("d_mid_credit").head(10)
    best  = m.sort_values("d_mid_credit", ascending=False).head(10)

    def fmt_table(df, title):
        rows = "\n".join(
            f"<tr><td>{r['ticker']}</td>"
            f"<td>{r['spread_type'].replace('_',' ')}</td>"
            f"<td>${r['short_strike']:.2f}/${r['long_strike']:.2f}</td>"
            f"<td>${r['mid_credit_a']:.3f}</td>"
            f"<td>${r['mid_credit_b']:.3f}</td>"
            f"<td class='{'neg' if r['d_mid_credit']<0 else 'pos'}'>${r['d_mid_credit']:+.3f}</td>"
            f"<td>{r['GROUND_a']*100:+.2f}%</td>"
            f"<td>{r['GROUND_b']*100:+.2f}%</td>"
            f"<td class='{'neg' if r['d_GROUND']<0 else 'pos'}'>{r['d_GROUND']*100:+.3f}pp</td>"
            f"</tr>"
            for _, r in df.iterrows()
        )
        return f"""<h2>{title}</h2>
<table>
<tr><th>ticker</th><th>direction</th><th>strikes</th>
    <th>mid_a</th><th>mid_b</th><th>Δ mid</th>
    <th>Γᵢ_a</th><th>Γᵢ_b</th><th>Δ Γᵢ</th></tr>
{rows}
</table>"""

    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>drift {tag}</title>
<style>
body {{ background:#0f0f0f; color:#e8e8e8; font:14px/1.4 -apple-system,sans-serif;
       max-width:1200px; margin:24px auto; padding:0 16px; }}
h1, h2 {{ color:#fff; font-weight:600; }}
.subtitle {{ color:#888780; font-size:13px; margin-bottom:24px; }}
.stats {{ display:grid; grid-template-columns:repeat(4,1fr); gap:16px;
          background:#1a1a1a; border:1px solid #333; padding:16px;
          border-radius:6px; margin-bottom:24px; }}
.stat-label {{ color:#888780; font-size:11px; text-transform:uppercase; }}
.stat-value {{ color:#fff; font-size:20px; font-weight:600; margin-top:4px; }}
table {{ border-collapse:collapse; width:100%; margin-bottom:24px; }}
th, td {{ padding:8px 12px; text-align:left; border-bottom:1px solid #2a2a2a; }}
th {{ color:#888780; font-size:11px; text-transform:uppercase; font-weight:500; }}
td {{ font-variant-numeric:tabular-nums; }}
.neg {{ color:#E24B4A; }}
.pos {{ color:#1D9E75; }}
</style></head><body>

<h1>drift · {tag}</h1>
<div class="subtitle">spreads matched in both snapshots: <b>{len(m):,}</b></div>

<div class="stats">
  <div class="stat"><div class="stat-label">median Δ mid credit</div><div class="stat-value">${m['d_mid_credit'].median():+.4f}</div></div>
  <div class="stat"><div class="stat-label">median Δ Γᵢ</div><div class="stat-value">{m['d_GROUND'].median()*100:+.3f}pp</div></div>
  <div class="stat"><div class="stat-label">median Δ short bid</div><div class="stat-value">${m['d_short_bid'].median():+.4f}</div></div>
  <div class="stat"><div class="stat-label">median Δ long ask</div><div class="stat-value">${m['d_long_ask'].median():+.4f}</div></div>
</div>

{fmt_table(worst, "top 10 spreads where mid credit moved MOST AGAINST you")}
{fmt_table(best,  "top 10 spreads where mid credit moved MOST IN your favor")}

</body></html>"""


if __name__ == "__main__":
    sys.exit(main())
