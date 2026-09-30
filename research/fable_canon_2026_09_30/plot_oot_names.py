"""Frequency of names picked in the 2026 OOT book (strategy C, k=24), split bull put / bear call."""
from pathlib import Path
import pandas as pd, matplotlib
matplotlib.use("Agg"); import matplotlib.pyplot as plt
HERE = Path(__file__).resolve().parent
p = pd.read_parquet(HERE / "picks_C_252_k24.parquet"); p = p[p.entry_date.dt.year == 2026]
t = p.groupby(["ticker", "spread_type"]).size().unstack(fill_value=0).reindex(columns=["bull_put", "bear_call"], fill_value=0)
t["total"] = t.sum(1); t = t.sort_values("total", ascending=True)
pnl = p.groupby("ticker").pnl_per_contract.sum()
SURF, INK, INK2, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e8e7e3"; C_BULL, C_BEAR = "#2a78d6", "#eb6834"
n = len(t); fig, ax = plt.subplots(figsize=(7.2, 0.26 * n + 1.6), dpi=180, facecolor=SURF); ax.set_facecolor(SURF)
y = range(n)
ax.barh(y, t.bull_put, color=C_BULL, height=0.62, label="bull put", zorder=3)
ax.barh(y, t.bear_call, left=t.bull_put, color=C_BEAR, height=0.62, label="bear call", zorder=3, edgecolor=SURF, linewidth=1)
for i, (tk, r) in enumerate(t.iterrows()):
    ax.text(r.total + 0.6, i, f"{int(r.total)}  ({'+' if pnl[tk] >= 0 else '−'}${abs(pnl[tk]):,.0f})", va="center", fontsize=7.2, color=INK2)
ax.set_yticks(list(y)); ax.set_yticklabels(t.index, fontsize=7.4, color=INK)
ax.set_xlabel("picks, 2026-01-05 to 2026-09-25", color=INK2, fontsize=9); ax.tick_params(axis="x", colors=INK2, labelsize=8)
ax.grid(axis="x", color=GRID, lw=1); ax.set_axisbelow(True)
for s in ("top", "right"): ax.spines[s].set_visible(False)
for s in ("left", "bottom"): ax.spines[s].set_color(GRID)
ax.set_xlim(0, t.total.max() * 1.22)
ax.set_title(f"2026 OOT picks by name: {len(p)} picks, {n} names (strategy C, k=24)\nlabel = picks (qty-1 P&L)", loc="left", fontsize=10, color=INK, pad=8)
ax.legend(frameon=False, fontsize=8.5, loc="lower right", labelcolor=INK)
plt.tight_layout(); out = HERE / "oot_2026_names.png"; fig.savefig(out, facecolor=SURF); print(out, n, "names")
print(t.sort_values("total", ascending=False).head(12).to_string())
