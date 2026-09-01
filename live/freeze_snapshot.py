"""Write the daily frozen snapshot, with a 15:31 top-up for short 15:01s.

Policy:
  - First 15:xx scan writes live/frozen/YYYY-MM-DD.json if missing.
  - If that freeze has 0 picks, a later 15:xx scan may replace it with the
    current ranked snapshot if the current snapshot has top_picks.
  - If that freeze has 1-4 picks, a later 15:xx scan may append unique picks
    from the current ranked snapshot until the basket reaches TOP_N_DISPLAY.
  - Existing 15:01 picks are never replaced or reordered.
"""
from __future__ import annotations

import argparse
import json
import os
import tempfile
from datetime import date, datetime
from pathlib import Path

import pandas as pd

import spreads
from live import live_config


ROOT = Path(__file__).resolve().parents[1]


def _pick_key(pick: dict) -> tuple:
    return (
        pick.get("ticker"),
        pick.get("spread_type"),
        pick.get("expiry_date"),
        pick.get("short_strike"),
        pick.get("long_strike"),
    )


def _dedupe_key(pick: dict):
    """Top-up dedupe key: TICKER only.

    The live ranker already keeps one direction per ticker within a scan, but
    the 15:31 top-up merges picks from a *different* scan. Keying on the full
    spread identity (_pick_key) let the same name in twice at different strikes
    — e.g. 2026-08-04 took CAT bull_put 892.5/890.0 at 15:01 and CAT bull_put
    887.5/885.0 at 15:32, doubling exposure on one underlying; 2026-06-15 got
    ISRG in BOTH directions at the same 415 strike. One bet per ticker per day.
    """
    tk = pick.get("ticker")
    return tk.strip().upper() if isinstance(tk, str) else tk


def _with_added_marker(pick: dict, hhmm: str) -> dict:
    out = dict(pick)
    out["freeze_added_at"] = hhmm
    return out


def _latest_hhmm(latest: dict, fallback: datetime) -> str:
    snap_file = str(latest.get("snapshot_file") or "")
    stem = Path(snap_file).stem
    if len(stem) == 4 and stem.isdigit():
        return f"{stem[:2]}:{stem[2:]}"
    snap_ts = latest.get("snapshot_ts")
    if isinstance(snap_ts, str):
        try:
            return datetime.fromisoformat(snap_ts).strftime("%H:%M")
        except ValueError:
            pass
    return fallback.strftime("%H:%M")


def _read_json(path: Path) -> dict | None:
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def _atomic_write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(payload, f, indent=2)
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def maybe_freeze(latest_path: Path | None = None, now: datetime | None = None) -> int:
    now = now or datetime.now()
    latest_path = latest_path or Path(live_config.RANKED_DIR) / "latest.json"
    dst = Path(live_config.FROZEN_DIR) / f"{now:%Y-%m-%d}.json"

    latest = _read_json(latest_path)
    if latest is None:
        print(f"[freeze] latest snapshot unreadable: {latest_path}", flush=True)
        return 1

    latest_picks = latest.get("top_picks") or []
    latest_hhmm = _latest_hhmm(latest, now)
    existing = _read_json(dst) if dst.exists() else None
    if existing is not None and (existing.get("top_picks") or []):
        existing_picks = existing.get("top_picks") or []
        target = int(getattr(live_config, "TOP_N_DISPLAY", 5))
        if len(existing_picks) >= target:
            print(f"[freeze] keep existing {dst} ({len(existing_picks)} picks)", flush=True)
            return 0
        if not latest_picks:
            print(f"[freeze] keep short {dst} ({len(existing_picks)} picks); latest has 0 picks", flush=True)
            return 0

        seen = {_dedupe_key(p) for p in existing_picks}
        additions = []
        for pick in latest_picks:
            key = _dedupe_key(pick)
            if key in seen:
                continue
            additions.append(_with_added_marker(pick, latest_hhmm))
            seen.add(key)
            if len(existing_picks) + len(additions) >= target:
                break

        if not additions:
            print(f"[freeze] keep short {dst} ({len(existing_picks)} picks); no unique latest picks", flush=True)
            return 0

        payload = dict(existing)
        payload["top_picks"] = existing_picks + additions
        original_at = str(payload.get("frozen_at") or live_config.FREEZE_AT)
        if "+" not in original_at:
            payload["frozen_at"] = f"{original_at}+{latest_hhmm}"
        payload["freeze_topup_from"] = latest.get("snapshot_file")
        payload["freeze_topup_at"] = latest_hhmm
        payload["freeze_topup_original_count"] = len(existing_picks)
        payload["freeze_topup_added_count"] = len(additions)
        payload["mock"] = False
        _atomic_write(dst, payload)
        print(
            f"[freeze] topped up {dst}: {len(existing_picks)} + {len(additions)} = {len(payload['top_picks'])} picks",
            flush=True,
        )
        return 0

    if existing is not None and not latest_picks:
        print(f"[freeze] keep blank {dst}; latest also has 0 picks", flush=True)
        return 0

    payload = dict(latest)
    payload["frozen_at"] = latest_hhmm
    payload["mock"] = False
    if existing is not None:
        payload["freeze_fallback_from"] = existing.get("snapshot_file")
        payload["freeze_fallback_at"] = latest_hhmm
        action = "replaced blank"
    else:
        action = "wrote"

    _atomic_write(dst, payload)
    print(f"[freeze] {action} {dst} ({len(latest_picks)} picks)", flush=True)
    return 0


def _daily_close(ticker: str, expiry: str) -> float | None:
    path = ROOT / "data" / "daily_bars_yahoo" / f"{ticker}.csv"
    if not path.exists():
        return None
    try:
        bars = pd.read_csv(path, usecols=["date", "close"])
    except Exception:
        return None
    row = bars[bars["date"] == expiry]
    if row.empty:
        return None
    return float(row["close"].iloc[0])


def _settle_payload_from_daily_closes(payload: dict) -> bool:
    picks = payload.get("top_picks") or []
    if not picks:
        return False
    exp = (picks[0].get("expiry_date") or "")[:10]
    try:
        if datetime.fromisoformat(exp).date() > date.today():
            return False
    except ValueError:
        return False

    wins = losses = partials = 0
    total = 0.0
    results = {}
    for pick in picks:
        tk = pick["ticker"]
        close = _daily_close(tk, exp)
        if close is None:
            results[tk] = {"error": "no close price"}
            continue

        spread_type = pick["spread_type"]
        short_k = float(pick["short_strike"])
        long_k = float(pick["long_strike"])
        mid_credit = float(pick["net_credit"])
        mid_ml = float(pick["max_loss"])
        width = mid_credit + mid_ml
        sb = pick.get("short_bid"); sa = pick.get("short_ask")
        lb = pick.get("long_bid"); la = pick.get("long_ask")
        if None not in (sb, sa, lb, la) and float(sa) > 0 and float(la) > 0:
            qmid = (float(sb) + float(sa)) / 2.0 - (float(lb) + float(la)) / 2.0
            credit = round(max(qmid, 0.0) * 0.80, 4)
        else:
            credit = round(mid_credit * 0.80, 4)
        credit = min(credit, round(width, 4))
        max_loss = round(width - credit, 4)

        outcome_code = spreads.calc_outcome(close, short_k, long_k, spread_type)
        pnl = spreads.calc_pnl(close, short_k, long_k, credit, max_loss, spread_type)
        pnl_ctr = round(float(pnl) * 100, 2)
        if outcome_code == 1.0:
            label = "WIN"; wins += 1
        elif outcome_code == -1.0:
            label = "LOSS"; losses += 1
        else:
            label = "PARTIAL"; partials += 1
        total += pnl_ctr
        results[tk] = {
            "underlying_price": round(close, 4),
            "outcome_code": float(outcome_code),
            "result": label,
            "pnl_per_share": round(float(pnl), 4),
            "pnl_per_contract": pnl_ctr,
        }

    payload["outcome"] = {
        "settled_at": datetime.now().isoformat(timespec="seconds"),
        "source": "backfill_1531_daily_closes",
        "results": results,
        "wins": wins,
        "losses": losses,
        "partials": partials,
        "total_pnl_per_contract": round(total, 2),
    }
    return True


def backfill_short_1501() -> int:
    changed = 0
    frozen_dir = Path(live_config.FROZEN_DIR)
    ranked_dir = Path(live_config.RANKED_DIR)
    for dst in sorted(frozen_dir.glob("*.json")):
        existing = _read_json(dst)
        if existing is None:
            continue
        day = dst.stem
        existing_picks = existing.get("top_picks") or []
        target = int(getattr(live_config, "TOP_N_DISPLAY", 5))
        if len(existing_picks) >= target:
            continue

        ranked_path = ranked_dir / f"{day}_1531.json"
        ranked = _read_json(ranked_path)
        picks = (ranked or {}).get("top_picks") or []
        if not picks:
            print(f"[backfill] {day}: no 15:31 picks; keep {len(existing_picks)} pick(s)", flush=True)
            continue

        if not existing_picks:
            payload = dict(ranked)
            payload["frozen_at"] = "15:31"
            payload["mock"] = False
            payload["freeze_fallback_from"] = existing.get("snapshot_file")
            payload["freeze_fallback_at"] = "15:31"
            added = len(picks)
            action = f"replaced blank with {added} 15:31 pick(s)"
        else:
            seen = {_dedupe_key(p) for p in existing_picks}
            additions = []
            for pick in picks:
                key = _dedupe_key(pick)
                if key in seen:
                    continue
                additions.append(_with_added_marker(pick, "15:31"))
                seen.add(key)
                if len(existing_picks) + len(additions) >= target:
                    break
            if not additions:
                print(f"[backfill] {day}: no unique 15:31 picks; keep {len(existing_picks)} pick(s)", flush=True)
                continue
            payload = dict(existing)
            payload["top_picks"] = existing_picks + additions
            original_at = str(payload.get("frozen_at") or live_config.FREEZE_AT)
            if "+" not in original_at:
                payload["frozen_at"] = f"{original_at}+15:31"
            payload["mock"] = False
            payload["freeze_topup_from"] = ranked.get("snapshot_file")
            payload["freeze_topup_at"] = "15:31"
            payload["freeze_topup_original_count"] = len(existing_picks)
            payload["freeze_topup_added_count"] = len(additions)
            added = len(additions)
            action = f"topped up {len(existing_picks)} + {added} = {len(payload['top_picks'])} pick(s)"

        _settle_payload_from_daily_closes(payload)
        _atomic_write(dst, payload)
        changed += 1
        print(f"[backfill] {day}: {action}", flush=True)
    print(f"[backfill] changed {changed} frozen file(s)", flush=True)
    return 0


def backfill_blank_1501() -> int:
    return backfill_short_1501()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--latest", type=Path, default=None)
    parser.add_argument("--backfill-blank-1501", action="store_true")
    parser.add_argument("--backfill-short-1501", action="store_true")
    args = parser.parse_args()
    if args.backfill_blank_1501 or args.backfill_short_1501:
        return backfill_short_1501()
    return maybe_freeze(args.latest)


if __name__ == "__main__":
    raise SystemExit(main())
