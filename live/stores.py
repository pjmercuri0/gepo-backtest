"""Two Actuals books, one scan (user 2026-10-30): the credit site (gepo-ticker) and the debit site
(gepo-debit-ticker, GEPO_SITE=debit) run the same webapp off the same ranked payloads. Each keeps its
own Actuals store and marks file; the debit site shows every pick as its parity debit twin."""
from __future__ import annotations
import os

ACTUALS = ("actuals.json", "actuals_debit.json")
MARKS = {"actuals.json": "actuals_marks.json", "actuals_debit.json": "actuals_marks_debit.json"}
IBKR = {"credit": "ibkr_actual_equity.json", "debit": "ibkr_actual_equity_debit.json"}


def site() -> str:
    return "debit" if os.environ.get("GEPO_SITE", "").strip().lower() == "debit" else "credit"


def is_debit_site() -> bool:
    return site() == "debit"


def actuals_name(which: str | None = None) -> str:
    return "actuals_debit.json" if (which or site()) == "debit" else "actuals.json"


def marks_name(which: str | None = None) -> str:
    return MARKS[actuals_name(which)]


def qkey(p: dict) -> str:
    """combo_stream quote key. A debit row (structure 'debit') streams its parity twin under the same
    name/strikes, so it carries a '|debit' suffix and cannot collide with the credit bag."""
    k = (f"{p.get('ticker')}|{p.get('spread_type')}|{float(p['short_strike']):g}"
         f"|{float(p['long_strike']):g}|{str(p.get('expiry_date'))[:10]}")
    return k + "|debit" if (p or {}).get("structure") == "debit" else k
