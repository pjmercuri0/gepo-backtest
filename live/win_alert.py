"""Telegram alert for the GREEN Actuals rows (user 2026-11-10: "add an alert for the green highlights
in actuals ... 90% or whatever we had it light up green").

Runs on Mya, once a minute in market hours from live/notify_watcher.sh, with the Python she has
(3.10, stdlib only). It asks the DEBIT site itself which rows it paints green right now
(/api/actuals/wins.json, built from the same _actuals_view the page renders) and writes one
notification file per NEW green row to live/notifications/, which notify_watcher.sh forwards to
Telegram in the same run. A row alerts once: the ledger .win_alerted.json remembers it by trade id
until its expiry passes, so a mark wobbling around 90% cannot spam.

  python3 live/win_alert.py            # 09:30-16:00 ET weekdays only
  python3 live/win_alert.py --force    # ignore the clock (test)
"""
from __future__ import annotations
import argparse, json, os, sys, tempfile, urllib.request
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
NOTIF = Path(os.environ.get("GEPO_NOTIF_DIR") or (HERE / "notifications"))   # env: test dir
LEDGER = NOTIF / ".win_alerted.json"
URL = os.environ.get("GEPO_WINS_URL", "http://127.0.0.1:3123/api/actuals/wins.json")
ET = ZoneInfo("America/New_York")


def _atomic_write(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp_", suffix=".json")
    with os.fdopen(fd, "w") as f:
        json.dump(payload, f, indent=2)
    os.replace(tmp, path)


def _fmt(w) -> str:
    k = w["ticker"]
    ss, ls = float(w["short_strike"]), float(w["long_strike"])
    exp = w.get("expiry_date") or ""
    try:
        exp = datetime.strptime(exp, "%Y-%m-%d").strftime("%b %-d")
    except ValueError:
        pass
    mark = w.get("mark"); width = w.get("width"); pnl = w.get("row_pnl"); fill = w.get("fill")
    s = f"- {k} {ss:g}/{ls:g} {w.get('label') or ''} {exp}: {w.get('pct'):.0f}% of width"
    if mark is not None and width is not None:
        s += f" (mark {float(mark):.2f} of {float(width):.2f}"
        s += f", fill {float(fill):.2f})" if fill is not None else ")"
    if pnl is not None:
        s += f", P&L {float(pnl):+.0f}"
    return s


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="run outside 09:30-16:00 ET")
    ap.add_argument("--url", default=URL)
    args = ap.parse_args()

    now = datetime.now(ET)
    hm = now.strftime("%H:%M")
    if not args.force and (now.weekday() >= 5 or not ("09:30" <= hm < "16:00")):
        return 0

    try:
        with urllib.request.urlopen(args.url, timeout=25) as r:
            data = json.loads(r.read())
    except Exception as e:
        print(f"{now:%Y-%m-%d %H:%M:%S} wins.json unreachable: {e}", flush=True)
        return 0
    wins = [w for w in (data.get("wins") or []) if w.get("id")]

    try:
        ledger = json.loads(LEDGER.read_text())
        if not isinstance(ledger, dict):
            ledger = {}
    except (OSError, json.JSONDecodeError):
        ledger = {}
    today = date.today().isoformat()
    ledger = {k: v for k, v in ledger.items() if str(v) >= today}      # expired rows roll off

    fresh = [w for w in wins if w["id"] not in ledger]
    if not fresh:
        if wins:
            print(f"{now:%Y-%m-%d %H:%M:%S} {len(wins)} green, all alerted", flush=True)
        return 0

    stamp = now.strftime("%Y-%m-%d_%H%M%S")
    out = NOTIF / f"win_alert_{stamp}.json"
    n = 1
    while out.exists():
        out = NOTIF / f"win_alert_{stamp}_{n}.json"; n += 1
    payload = {
        "type": "win-alert",
        "ts": now.isoformat(timespec="seconds"),
        "site": data.get("site"),
        "green_pct": data.get("green_pct"),
        "stream_ts": data.get("stream_ts"),
        "positions": fresh,
        "message": (f"✅ gepo {data.get('green_pct', 90)}%+ of width (green on Actuals)\n"
                    + "\n".join(_fmt(w) for w in fresh)),
    }
    _atomic_write(out, payload)
    for w in fresh:
        ledger[w["id"]] = w.get("expiry_date") or today
    _atomic_write(LEDGER, ledger)
    print(f"{now:%Y-%m-%d %H:%M:%S} ALERT {len(fresh)} new green -> {out.name}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
