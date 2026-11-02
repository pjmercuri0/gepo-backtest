"""Credit book vs call-twin (debit) book, off the stream's raw tick history (user 2026-11-02:
'track some last prices on both books and see'). live/logs/combo_hist_YYYYMMDD.jsonl holds, per
second, every held bag's exchange/bid/ask (and, from 2026-11-02 12:xx, last): credit bags under
their key, the parity twin under key|debit. Everything is reported on the CREDIT scale so the two
books compare directly: the credit spread's credit, and width - the debit twin's debit (puts or calls depending on the side).
  hit  = what you get selling into the book right now (credit: -ask; debit: width - ask)
  mid  = the book midpoint
  last = the bag's last trade on that exchange, when IB prints one (sparse)
Usage: compare_books.py [YYYYMMDD] [--min-secs 60]
"""
import json, sys, collections, statistics as st
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
day = next((a for a in sys.argv[1:] if a.isdigit()), None) or __import__("datetime").date.today().strftime("%Y%m%d")
min_secs = int(sys.argv[sys.argv.index("--min-secs") + 1]) if "--min-secs" in sys.argv else 60
f = ROOT / "live" / "logs" / f"combo_hist_{day}.jsonl"
MAXW = 1.0            # a book wider than the spread is not a price (stream rule)

def width_of(key):
    p = key.split("|"); return abs(float(p[2]) - float(p[3]))

acc = collections.defaultdict(lambda: {"credit": [], "debit": [], "last_c": [], "last_d": []})
for line in open(f):
    try: d = json.loads(line)
    except Exception: continue
    for k, v in (d.get("bags") or {}).items():
        ex, bid, ask = v[0], v[1], v[2]
        last = v[3] if len(v) > 3 else None
        if bid is None or ask is None: continue
        deb = k.endswith("|debit"); base = k[:-6] if deb else k
        w = width_of(base)
        if deb:
            if not (0 <= bid <= w + 1e-9 and 0 <= ask <= w + 1e-9) or (ask - bid) > MAXW * w: continue
            hit, mid = w - ask, w - (bid + ask) / 2
            if last is not None and 0 < last <= w: acc[base]["last_d"].append((d["ts"], w - last))
            acc[base]["debit"].append((hit, mid))
        else:
            cb, ca = -bid, -ask
            if not (0 <= cb <= w + 1e-9 and 0 <= ca <= w + 1e-9) or abs(cb - ca) > MAXW * w: continue
            hit, mid = ca, (cb + ca) / 2
            if last is not None and 0 < -last <= w: acc[base]["last_c"].append((d["ts"], -last))
            acc[base]["credit"].append((hit, mid))

rows = []
for k, a in acc.items():
    if len(a["credit"]) < min_secs or len(a["debit"]) < min_secs: continue
    ch, cm = st.mean(x[0] for x in a["credit"]), st.mean(x[1] for x in a["credit"])
    dh, dm = st.mean(x[0] for x in a["debit"]), st.mean(x[1] for x in a["debit"])
    rows.append((k, width_of(k), len(a["credit"]), len(a["debit"]), ch, dh, cm, dm,
                 len(a["last_c"]), len(a["last_d"]),
                 st.mean(x[1] for x in a["last_c"]) if a["last_c"] else None,
                 st.mean(x[1] for x in a["last_d"]) if a["last_d"] else None))
rows.sort(key=lambda r: r[0])
print(f"{day}: {len(rows)} spreads with both books quoted >= {min_secs}s  (credit scale: the credit book's credit vs width - the debit book's debit)")
print(f"{'spread':34} {'w':>4} {'secs c/d':>11} {'hit cr':>8} {'hit deb':>9} {'mid cr':>8} {'mid deb':>9} {'lasts c/d':>10} {'last cr':>9} {'last deb':>10}")
for r in rows:
    k, w, nc, nd, ch, dh, cm, dm, lc, ld, lcm, ldm = r
    print(f"{k:34} {w:4.2f} {nc:5d}/{nd:<5d} {ch:8.3f} {dh:9.3f} {cm:8.3f} {dm:9.3f} {lc:4d}/{ld:<5d} "
          f"{(f'{lcm:.3f}' if lcm is not None else '—'):>9} {(f'{ldm:.3f}' if ldm is not None else '—'):>10}")
if rows:
    dh = [r[5] - r[4] for r in rows]; dm = [r[7] - r[6] for r in rows]
    print(f"\ndebit book minus credit book, mean over spreads: hit {st.mean(dh):+.3f}  mid {st.mean(dm):+.3f}   "
          f"(debit better on hit: {sum(1 for x in dh if x > 0)}/{len(dh)}, on mid: {sum(1 for x in dm if x > 0)}/{len(dm)})")
