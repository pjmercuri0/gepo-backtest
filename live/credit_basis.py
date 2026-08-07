"""Canonical entry-credit basis for a frozen pick.

Single source of truth for "what credit did we take in at?", so the live
modules cannot drift apart again.

Canon (2026-06-10, reaffirmed 2026-06-12):
    actual_credit           when the user recorded a real broker fill
    else 0.80 x combo MID   recomputed from the freeze-time leg quotes
    else 0.80 x net_credit  when leg quotes are unusable
    ...always clamped to the spread width.

Why MID and not LAST: the LAST basis is dead (async leg prints fabricate
credits; 19.2% of same-day prints fall outside the EOD BBO). It survived in
the tracker/close-alert paths after webapp.py and expire_frozen.py moved to
mid, which let a single pick report two different entry credits. Concrete
case: BLK bear_call 1140/1145 on 2026-08-04 had short_last 18.30 / long_last
10.05 -> a LAST credit of 5.45 on a 5.00-wide spread. Impossible for a
vertical; it clamped to width and produced an entry credit of 4.00 (P&L
+$400) where the canonical mid basis gives 2.36 (P&L +$236).

0.80 is calibrated on the only real fills on record (2026-05-28, n=5, mean
0.82x mid). Never calibrate on estimated actual_credit entries.
"""
from __future__ import annotations

FILL_FRAC = 0.80


def spread_width(pick: dict) -> float:
    """Width of the vertical, from the stored mid credit + mid max-loss."""
    return float(pick.get("net_credit") or 0) + float(pick.get("max_loss") or 0)


def entry_credit(pick: dict) -> float:
    """Canonical per-share entry credit for a frozen pick.

    Mirrors webapp._enrich_pick and expire_frozen so every surface agrees.
    """
    width = spread_width(pick)

    actual = pick.get("actual_credit")
    if actual is not None:
        try:
            return min(round(float(actual), 4), round(width, 4))
        except (TypeError, ValueError):
            pass  # fall through to the quote-derived basis

    sb, sa = pick.get("short_bid"), pick.get("short_ask")
    lb, la = pick.get("long_bid"), pick.get("long_ask")
    if None not in (sb, sa, lb, la):
        try:
            if float(sa) > 0 and float(la) > 0:
                qmid = (float(sb) + float(sa)) / 2.0 - (float(lb) + float(la)) / 2.0
                return min(round(max(qmid, 0.0) * FILL_FRAC, 4), round(width, 4))
        except (TypeError, ValueError):
            pass

    mid_credit = float(pick.get("net_credit") or 0)
    return min(round(mid_credit * FILL_FRAC, 4), round(width, 4))
