"""Flask webapp for the GEPO live tracker.

Routes:
  GET  /                    — live page (auto-polls /api/latest.json)
  GET  /history             — list of frozen 15:45 daily snapshots
  GET  /api/latest.json     — most recent ranked snapshot (frozen flag set if past 15:45)
  GET  /api/notifications/latest — most recent freeze payload (for Mya pickup)

Run:
  python -m live.webapp           # http://127.0.0.1:5050
"""
from __future__ import annotations
import json
import math
import os
import sys
import tempfile
from datetime import datetime, time as dtime, date as ddate
from pathlib import Path

from flask import Flask, jsonify, render_template, abort, request, send_from_directory

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from live import live_config
from live.regime import current_regime
from live import trading_calendar
import spreads


app = Flask(
    __name__,
    template_folder=str(Path(__file__).parent / "templates"),
    static_folder=str(Path(__file__).parent / "static"),
)
# Pick up template + CSS edits on the next request without restarting Flask.
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.jinja_env.auto_reload = True
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0   # static files served with no-cache


# Half-away-from-zero rounding (NOT Python's banker's rounding). Use this
# for any monetary display so 172.5 rounds to 173, not 172.
def _round_half_up(x, digits=0):
    if x is None:
        return None
    mult = 10 ** digits
    f = float(x)
    if f >= 0:
        return math.floor(f * mult + 0.5) / mult
    return -math.floor(-f * mult + 0.5) / mult


app.jinja_env.filters['rd'] = _round_half_up


# ── Helpers ────────────────────────────────────────────────────────────────

def _read_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def _atomic_write_json(path: Path, payload: dict) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(payload, f, indent=2)
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _is_past_freeze() -> bool:
    """True if wall-clock is past the FREEZE_AT time on a weekday.

    Uses local time. The fetcher and freezer are also scheduled in local time,
    so this is consistent.
    """
    now = datetime.now()
    if now.weekday() >= 5:  # weekend
        return False
    hh, mm = (int(x) for x in live_config.FREEZE_AT.split(":"))
    return now.time() >= dtime(hour=hh, minute=mm)


def _frozen_payload_today() -> dict | None:
    today = datetime.now().date().isoformat()
    return _read_json(Path(live_config.FROZEN_DIR) / f"{today}.json")


def _enrich_pick(pick: dict, tracking_rows: list = None, credit_frac: float = 1.0) -> None:
    """Mutate pick in-place to add natural_net + fill_targets.

    natural_net = short_bid − long_ask. Sourced from the pick itself or, if
    absent, from the first tracking row with bid/ask.

    fill_targets[0] = primary limit-order credit basis. Canonical (2026-06-10):
    the combo mid itself (net_credit under raw-mid selection). Real fills run
    ~0.82×mid (n=5, 2026-05-28) but the displayed target matches the scoring
    basis so ratio/Kelly EV/credit columns are mutually consistent.
    Spread width is invariant; max_loss = spread_width − credit.
    """
    sb = pick.get("short_bid")
    la = pick.get("long_ask")
    if sb is None or la is None:
        for r in tracking_rows or []:
            sb = r.get("short_bid"); la = r.get("long_ask")
            if sb is not None and la is not None:
                break
    if sb is not None and la is not None:
        pick["natural_net"] = round(float(sb) - float(la), 4)

    mid_c = float(pick.get("net_credit") or 0)
    ml    = float(pick.get("max_loss") or 0)
    spread_w = mid_c + ml

    # Canonical 2026-06-10: display credit = combo mid. New-canon picks have
    # net_credit == mid already; OLD frozen picks (pre-2026-06-10) stored the
    # phantom LAST-clamped credit (e.g. 2.45 on a 2.50 width → max-loss 0.05,
    # ratio 49). When the freeze-time leg quotes are present, recompute the
    # honest mid from them; frozen files stay immutable, only display changes.
    sb_q = pick.get("short_bid"); sa_q = pick.get("short_ask")
    lb_q = pick.get("long_bid");  la_q = pick.get("long_ask")
    if pick.get("combo_priced") and mid_c > 0:
        # Take net_credit as the ranker set it. Do NOT re-derive from
        # combo_credit_mid: on a too-wide book the ranker deliberately prices
        # off the combo's LAST instead, and combo_credit_mid still holds the
        # useless wide-book midpoint. TMO Sep04 602.5/600 on 2026-09-01 15:38:
        # book -5.80/+1.90 (7.70 wide on a 2.50 spread), combo_credit_mid 1.95,
        # net_credit 0.93 from combo last — and 0.93 is IBKR's ticket price.
        # Preferred over the leg-mid recompute below because differencing two
        # leg mids inherits whatever junk sits on either leg (MO Sep04 70/69 at
        # 12:31 → 0.620 from a 50-lot 1.68 offer; IBKR's book mid was 0.485).
        base_mid = mid_c
        basis = "COMBO MID" if not pick.get("combo_too_wide") else "COMBO LAST"
    elif None not in (sb_q, sa_q, lb_q, la_q) and float(sa_q) > 0 and float(la_q) > 0:
        base_mid = max((float(sb_q) + float(sa_q)) / 2.0 - (float(lb_q) + float(la_q)) / 2.0, 0.0)
        basis = "MID"
    else:
        base_mid = mid_c
        basis = "MID (stored)"
    # credit_frac: 1.0 = selection view (live tab, raw mid); 0.80 = fill view
    # (history page) so displayed credit/max-loss bound the P&L, which is
    # computed from the 0.80×mid entry. actual_credit overrides in fill view.
    actual_c = pick.get("actual_credit")
    if credit_frac != 1.0 and actual_c is not None:
        c0 = round(float(actual_c), 4)
        basis = "ACTUAL"
    else:
        c0 = round(base_mid * credit_frac, 4)
        if credit_frac != 1.0:
            basis = f"{credit_frac:.2f}×{basis}"
    # Belt-and-suspenders: clamp credit basis to spread width regardless of path.
    c0 = min(c0, round(spread_w, 4))

    # Canonical display sizing 2026-06-05: qty=1 per pick (per-contract display).
    # Live trading qty stays at user's discretion; the displayed credit/risk/P&L
    # numbers are per-contract for clean reading.
    ml_after = round(spread_w - c0, 4)
    ml_dollar = ml_after * 100
    suggested_qty = 1

    pick["fill_basis"] = basis
    pick["fill_targets"] = [
        {"credit": c0, "max_loss": ml_after},
    ]
    pick["suggested_qty"] = suggested_qty
    pick["suggested_capital"] = round(suggested_qty * ml_dollar, 2)


def _enrich_payload(payload: dict) -> None:
    """Attach fill_targets + day-level midmid totals to every pick on a
    /api/latest.json-style payload (frozen or live). For frozen payloads the
    natural_net comes from tracking row bid/ask; for live ranker output it
    comes from the pick's own bid/ask fields (written by ranker.py)."""
    tracking = payload.get("tracking") or {}
    for pick in payload.get("top_picks") or []:
        _enrich_pick(pick, tracking_rows=tracking.get(pick.get("ticker")) or [])
    for pick in payload.get("ticker") or []:
        _enrich_pick(pick, tracking_rows=tracking.get(pick.get("ticker")) or [])
    payload.update(_basket_totals(payload.get("top_picks") or []))
    payload["week_notice"] = trading_calendar.week_notice()


def _basket_totals(picks: list) -> dict:
    """Sum credit + max_loss across picks at SUGGESTED_QTY (¹⁄₁₆ Kelly cap=5).
    Each pick contributes (credit × 100 × suggested_qty) and (max_loss × 100 × qty).
    Also sums actual fills if any pick has actual_credit. Returns dollar totals."""
    sum_c = sum_ml = 0.0
    sum_actual_c = sum_actual_ml = 0.0
    any_actual = False
    for p in picks or []:
        qty = max(1, int(p.get("suggested_qty") or 1))
        tgts = p.get("fill_targets") or []
        if tgts:
            sum_c  += float(tgts[0]["credit"]) * 100 * qty
            sum_ml += float(tgts[0]["max_loss"]) * 100 * qty
        else:
            sum_c  += float(p.get("net_credit") or 0) * 100 * qty
            sum_ml += float(p.get("max_loss") or 0) * 100 * qty
        actual_c = p.get("actual_credit")
        actual_ml = p.get("actual_max_loss")
        if actual_c is not None and actual_ml is not None:
            sum_actual_c += float(actual_c) * 100 * qty
            sum_actual_ml += float(actual_ml) * 100 * qty
            any_actual = True
        else:
            if tgts:
                sum_actual_c  += float(tgts[0]["credit"]) * 100 * qty
                sum_actual_ml += float(tgts[0]["max_loss"]) * 100 * qty
    out = {
        "day_sum_credit":   round(sum_c, 2) if sum_c else None,
        "day_sum_max_loss": round(sum_ml, 2) if sum_ml else None,
        "day_decimal_odds": round(sum_c / sum_ml, 3) if sum_ml > 0 else None,
    }
    if any_actual:
        out["day_sum_credit_actual"]   = round(sum_actual_c, 2) if sum_actual_c else None
        out["day_sum_max_loss_actual"] = round(sum_actual_ml, 2) if sum_actual_ml else None
        out["day_decimal_odds_actual"] = round(sum_actual_c / sum_actual_ml, 3) if sum_actual_ml > 0 else None
    return out


def _frozen_history(limit: int = 60) -> list[dict]:
    base = Path(live_config.FROZEN_DIR)
    if not base.exists():
        return []
    out = []
    for fp in sorted(base.glob("*.json"), reverse=True)[:limit]:
        # Skip Friday-entry frozen files (DTE=7 to next-Friday is zero-edge
        # per daily backtest). Filtering at the history level keeps any
        # existing Friday JSON on disk for archival but hides it from UI.
        try:
            dow = datetime.strptime(fp.stem, "%Y-%m-%d").weekday()
            if dow == 4:  # Friday
                continue
        except ValueError:
            pass
        payload = _read_json(fp)
        if payload is None:
            continue
        payload["date"] = fp.stem
        try:
            payload["date_dow"] = datetime.strptime(fp.stem, "%Y-%m-%d").strftime("%a")
        except ValueError:
            payload["date_dow"] = None
        out.append(payload)

    # Annotate each entry with its per-DAY MTM total: sum of that day's
    # picks' P&Ls (realized if settled, otherwise unrealized from latest
    # tracking row). Picks with neither an outcome nor any tracking row
    # contribute None. If NO pick on the day has any data, the day total
    # is None (rendered as "—" rather than a misleading $0).
    def _pick_pnl(pick: dict, day: dict):
        tk = pick.get("ticker")
        outcome = day.get("outcome") or {}
        results = outcome.get("results") or {}
        if tk in results and results[tk].get("pnl_per_contract") is not None:
            return float(results[tk]["pnl_per_contract"])
        # Walk backwards through tracking history for the most recent row
        # that actually has unrealized P&L (some rows are spot-only when
        # the option leg drops out of the daily snapshot).
        tracking = day.get("tracking") or {}
        rows = tracking.get(tk) or []
        for r in reversed(rows):
            if r.get("unrealized_pnl_per_contract") is not None:
                return float(r["unrealized_pnl_per_contract"])
        return None

    for d in out:
        if d.get("mock"):
            d["day_total_pnl_per_contract"] = None
            d["last_updated_ts"] = None
            d["day_sum_credit"] = None
            d["day_sum_max_loss"] = None
            d["day_decimal_odds"] = None
            continue
        # Unrealized P&L MODEL (canonical 2026-06-02):
        #   entry_credit  = 0.85 × clamped freeze-time LAST  (locked at entry)
        #   close_debit   = 1.15 × clamped CURRENT LAST       (if we closed now)
        #   live_status   = WINNING/PARTIAL/LOSING based on spot vs strikes
        # Matches the "close everything at 1.15×LAST on Friday" backtest.
        # WINNING (currently OTM): close_debit is near zero → MTM ≈ +entry_credit.
        # PARTIAL/LOSING: close_debit reflects current intrinsic + 15% slip.
        def _clamp_pair(sl, ll, sb, sa, lb, la):
            sl_eff = float(sl); ll_eff = float(ll)
            if sb is not None and sa is not None:
                sl_eff = max(float(sb), min(sl_eff, float(sa)))
            if lb is not None and la is not None:
                ll_eff = max(float(lb), min(ll_eff, float(la)))
            return sl_eff, ll_eff

        tracking_map = d.get("tracking") or {}
        for pick in d.get("top_picks") or []:
            tk = pick.get("ticker")
            rows = tracking_map.get(tk) or []
            spot = None
            target_row = None
            for r in reversed(rows):
                sp = r.get("underlying_price")
                if sp is not None:
                    spot = float(sp)
                    target_row = r
                    break
            if target_row is None:
                continue
            try:
                ss = float(pick["short_strike"])
                ls = float(pick["long_strike"])
                stype = pick["spread_type"]
            except (KeyError, TypeError, ValueError):
                continue
            mid_c = float(pick.get("net_credit") or 0)
            mid_ml = float(pick.get("max_loss") or 0)
            spread_w = mid_c + mid_ml

            # Entry credit: actual_credit if recorded, else 0.80 × freeze-time
            # combo mid (canon 2026-06-10; real fills ran ~0.82×mid, n=5).
            # Quote-derived mid also fixes OLD frozen picks whose stored
            # net_credit is the phantom LAST-clamped credit.
            actual_c = pick.get("actual_credit")
            if actual_c is not None:
                entry_credit = float(actual_c)
            else:
                fsb = pick.get("short_bid"); fsa = pick.get("short_ask")
                flb = pick.get("long_bid");  fla = pick.get("long_ask")
                if None not in (fsb, fsa, flb, fla) and float(fsa) > 0 and float(fla) > 0:
                    qmid = (float(fsb) + float(fsa)) / 2.0 - (float(flb) + float(fla)) / 2.0
                    entry_credit = min(max(qmid, 0.0), spread_w) * 0.80
                else:
                    entry_credit = min(mid_c * 0.80, spread_w)

            # Close debit: TRUST the tracker's mark (canonical 2026-06-04: BS_theo).
            # If the latest tick has no current_mark (legs missing from snapshot —
            # typically because fetcher rolled to next-week expiry on expiry day),
            # compute INTRINSIC at CURRENT spot. Walking back to a stale tick with
            # leg data would use yesterday's spot and miss intra-day moves.
            # Intrinsic at CURRENT spot is a hard floor on what closing costs:
            # the tracker's mark can be hours stale (tick rides the scan cadence)
            # while the spot column is live, which showed green P&L on a pick
            # whose live spot implied max loss (QCOM 2026-06-10).
            if stype == "bull_put":
                short_intr = max(0.0, ss - spot)
                long_intr  = max(0.0, ls - spot)
            else:  # bear_call
                short_intr = max(0.0, spot - ss)
                long_intr  = max(0.0, spot - ls)
            intrinsic = min(max(0.0, short_intr - long_intr), spread_w)
            close_debit = target_row.get("current_mark")
            close_debit = intrinsic if close_debit is None else max(float(close_debit), intrinsic)

            pps_per_share = entry_credit - close_debit
            target_row["unrealized_pnl_per_contract"] = round(pps_per_share * 100, 2)
            target_row["current_mark"] = round(close_debit, 4)

            # Live status — WINNING requires spot to clear short_strike by ≥$0.01.
            # Right AT the strike = PARTIAL (assignment risk if it closes there).
            if stype == "bull_put":
                if spot >= ss + 0.01:
                    target_row["live_status"] = "WINNING"
                elif spot <= ls:
                    target_row["live_status"] = "LOSING"
                else:
                    target_row["live_status"] = "PARTIAL"
            else:  # bear_call
                if spot <= ss - 0.01:
                    target_row["live_status"] = "WINNING"
                elif spot >= ls:
                    target_row["live_status"] = "LOSING"
                else:
                    target_row["live_status"] = "PARTIAL"
            # Assignment-risk flag (canonical 2026-06-05): set when today is the
            # pick's expiry day AND short leg is ITM (i.e., spot crossed short_strike
            # in the wrong direction). User must close to avoid weekend assignment.
            try:
                exp_d = datetime.fromisoformat(pick.get("expiry_date", "")).date()
            except (ValueError, TypeError):
                exp_d = None
            today_d = ddate.today()
            short_itm = (stype == "bull_put" and spot < ss) or \
                        (stype == "bear_call" and spot > ss)
            target_row["assignment_risk"] = bool(
                exp_d == today_d and today_d.weekday() == 4 and short_itm
            )
        total = None
        latest_ts = None
        for pick in d.get("top_picks") or []:
            rows = tracking_map.get(pick.get("ticker")) or []
            # Enrich FIRST so suggested_qty is set before we scale P&L by it.
            # History = fill view: credit/max-loss at 0.80×mid (or actual_credit)
            # so the displayed max-loss bounds the displayed P&L.
            _enrich_pick(pick, tracking_rows=rows, credit_frac=0.80)
            qty = max(1, int(pick.get("suggested_qty") or 1))
            pnl = _pick_pnl(pick, d)
            if pnl is not None:
                total = (total or 0.0) + pnl * qty
            for r in rows:
                ts = r.get("ts")
                if ts and (latest_ts is None or ts > latest_ts):
                    latest_ts = ts
        # Settlement writes outcome.settled_at but doesn't append a tracking
        # row, so include it in the "updated" timestamp consideration.
        settled_at = (d.get("outcome") or {}).get("settled_at")
        if settled_at and (latest_ts is None or settled_at > latest_ts):
            latest_ts = settled_at
        d["day_total_pnl_per_contract"] = round(total, 2) if total is not None else None
        d["last_updated_ts"] = latest_ts
        d.update(_basket_totals(d.get("top_picks") or []))
    return out


# ── Routes ─────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template(
        "index.html",
        poll_seconds=live_config.WEBAPP_POLL_SECONDS,
    )


@app.route("/backtest")
def backtest():
    """Static backtest tab: equity curve vs SPY (G_rv canon 2026-06-09).
    Data is precomputed and shipped to live/data/backtest_equity.json by
    report_three_sizings.py (rich payload: weeks/trades/sizing arms)."""
    payload = _read_json(Path(ROOT) / "live" / "data" / "backtest_equity.json")
    return render_template("backtest.html", data=payload)


@app.route("/oot")
def oot():
    """2026 out-of-time results: frozen canon applied to 2026 data.
    Payload written by report_oot_2026.py to live/data/oot_equity.json."""
    payload = _read_json(Path(ROOT) / "live" / "data" / "oot_equity.json")
    return render_template("oot.html", data=payload)


@app.route("/qr")
def qr():
    return render_template(
        "qr.html",
        site_url="https://gepo-ticker.peter.cloudmallinc.com/",
    )


def _round_hhmm(hhmm: str) -> str:
    """Round an HHMM string to the nearest half hour. Cron fires at :01/:31
    but the snapshot timestamp drifts a few minutes (10:04, 14:33), which
    splits one logical scan across multiple rows. Bucket to :00/:30 so the
    by-time aggregate collapses the drift."""
    try:
        total = int(hhmm[:2]) * 60 + int(hhmm[2:])
    except (ValueError, IndexError):
        return hhmm
    total = (int(round(total / 30.0)) * 30) % (24 * 60)
    return f"{total // 60:02d}{total % 60:02d}"


@app.route("/snapshots")
def snapshots():
    """Every :01/:31 scan's qualified picks, settled at expiry. Purpose:
    test whether the GROUND threshold picks well at ANY time of day or
    only at the 15:01 freeze that mirrors the backtest's EOD basis."""
    picks_dir = Path(live_config.ROOT_DIR) / "intraday_picks"
    days = []
    for fp in sorted(picks_dir.glob("*.json"), reverse=True):
        d = _read_json(fp)
        if d:
            for scan in d.get("scans", []):
                hhmm = scan.get("hhmm", "")
                scan["hhmm_actual"] = hhmm
                scan["hhmm_round"] = _round_hhmm(hhmm)
            days.append(d)
    # Aggregate settled picks by scan time-of-day, rounded to the half hour.
    by_time = {}
    for d in days:
        for scan in d.get("scans", []):
            key = scan["hhmm_round"]
            slot = by_time.setdefault(key, {
                "hhmm": key, "n": 0, "settled": 0,
                "pnl": 0.0, "wins": 0, "scans": 0,
            })
            slot["scans"] += 1
            for p in scan.get("picks", []):
                slot["n"] += 1
                if p.get("pnl") is not None:
                    slot["settled"] += 1
                    slot["pnl"] += float(p["pnl"])
                    if p["pnl"] > 0:
                        slot["wins"] += 1
    agg = sorted(by_time.values(), key=lambda s: s["hhmm"])
    for s in agg:
        s["pnl"] = round(s["pnl"], 2)
        s["win_rate"] = round(100.0 * s["wins"] / s["settled"], 1) if s["settled"] else None
        s["per_trade"] = round(s["pnl"] / s["settled"], 2) if s["settled"] else None
    import config as backtest_config
    return render_template("snapshots.html", days=days, agg=agg,
                           thr=backtest_config.GROUND_THRESHOLD)


@app.route("/history")
def history():
    # Load today's close alert (if any) so the template can show a
    # banner with rec_debit prices for any open expiring picks.
    today_iso = datetime.now().date().isoformat()
    close_alert = _read_json(
        Path(live_config.NOTIFICATIONS_DIR) / f"close_alert_{today_iso}.json"
    )
    return render_template("history.html",
                           entries=_frozen_history(),
                           close_alert=close_alert)


@app.route("/api/latest.json")
def latest_json():
    """Return the latest ranked snapshot.

    If we're past the freeze time and today's frozen file exists, return the
    frozen payload with `frozen=true` instead of the live latest. This makes
    the UI display the official 15:45 picks for the rest of the day.
    """
    if _is_past_freeze():
        frozen = _frozen_payload_today()
        if frozen is not None:
            frozen["frozen"] = True
            frozen.setdefault("frozen_at", live_config.FREEZE_AT)
            # Override baked-in regime with the live one so the subheader
            # tracks the IBKR tick (and the chip + subheader agree).
            frozen["regime"] = current_regime()
            _enrich_payload(frozen)
            return jsonify(frozen)

    latest_path = Path(live_config.RANKED_DIR) / "latest.json"
    payload = _read_json(latest_path)
    if payload is None:
        return jsonify({
            "snapshot_ts": None,
            "snapshot_file": None,
            "n_candidates": 0,
            "config": None,
            "top_picks": [],
            "ticker": [],
            "regime": current_regime(),
            "week_notice": trading_calendar.week_notice(),
            "error": "no ranked snapshot found yet — run the fetcher + ranker",
        })
    payload["frozen"] = False
    # Always re-evaluate regime against the freshest data on disk, so the
    # subheader matches the SPY chip even if `latest.json` was baked earlier.
    payload["regime"] = current_regime()
    _enrich_payload(payload)
    return jsonify(payload)


@app.route("/api/spy/latest.json")
def spy_latest():
    """Most recent live SPY tick (from local IBKR fetcher). Returns 404
    with an explanatory body if no fetch has been written yet."""
    path = Path(live_config.RANKED_DIR) / "spy_intraday.json"
    payload = _read_json(path)
    if payload is None:
        return jsonify({
            "error": "no SPY intraday snapshot yet — run `python3 -m live.fetch_spy_intraday`",
        }), 404
    return jsonify(payload)


@app.route("/api/notifications/latest")
def notifications_latest():
    """Most recent notification payload. Mya can poll this endpoint, or watch
    the live/notifications/ directory directly."""
    base = Path(live_config.NOTIFICATIONS_DIR)
    if not base.exists():
        return jsonify({"error": "no notifications directory"}), 404
    files = sorted(base.glob("*.json"), reverse=True)
    if not files:
        return jsonify({"error": "no notifications yet"}), 404
    payload = _read_json(files[0])
    if payload is None:
        abort(500)
    payload["_filename"] = files[0].name
    return jsonify(payload)


@app.route("/api/frozen/<date>.json")
def frozen_by_date(date: str):
    payload = _read_json(Path(live_config.FROZEN_DIR) / f"{date}.json")
    if payload is None:
        abort(404)
    return jsonify(payload)


@app.route("/api/frozen/<date>/<ticker>/actual_credit", methods=["POST"])
def set_actual_credit(date: str, ticker: str):
    """Set or clear the actual broker fill credit for a frozen pick.

    Body: {"actual_credit": 2.80}  or  {"actual_credit": null}  to clear.
    Updates pick.actual_credit + actual_max_loss. If the day is settled,
    also recomputes outcome.results[ticker].actual_pnl_per_* and the
    day-level outcome.total_pnl_per_contract_actual.
    """
    body = request.get_json(silent=True) or {}
    raw = body.get("actual_credit")
    if raw in (None, ""):
        new_val = None
    else:
        try:
            new_val = float(raw)
        except (TypeError, ValueError):
            return jsonify({"error": "actual_credit must be numeric or null"}), 400

    fp = Path(live_config.FROZEN_DIR) / f"{date}.json"
    if not fp.exists():
        abort(404)
    payload = _read_json(fp)
    if payload is None:
        return jsonify({"error": "could not read frozen file"}), 500

    picks = payload.get("top_picks") or []
    target = next((p for p in picks if p.get("ticker") == ticker), None)
    if target is None:
        abort(404)

    mid_credit = float(target.get("net_credit") or 0)
    mid_ml = float(target.get("max_loss") or 0)
    spread_w = mid_credit + mid_ml
    if spread_w <= 0:
        return jsonify({"error": "spread width unknown for this pick"}), 400

    if new_val is None:
        target.pop("actual_credit", None)
        target.pop("actual_max_loss", None)
    else:
        if new_val <= 0 or new_val >= spread_w:
            return jsonify({"error": f"actual_credit must be between 0 and spread width {spread_w}"}), 400
        target["actual_credit"] = round(new_val, 4)
        target["actual_max_loss"] = round(spread_w - new_val, 4)

    outcome = payload.get("outcome")
    if outcome and isinstance(outcome.get("results"), dict):
        results = outcome["results"]
        row = results.get(ticker)
        if row is not None:
            spot = row.get("underlying_price")
            stype = target.get("spread_type")
            ss = float(target.get("short_strike") or 0)
            ls = float(target.get("long_strike") or 0)
            if new_val is None or spot is None or stype is None or ss == 0 or ls == 0:
                row.pop("actual_pnl_per_share", None)
                row.pop("actual_pnl_per_contract", None)
            else:
                actual_ml = spread_w - new_val
                actual_pps = spreads.calc_pnl(float(spot), ss, ls,
                                               float(new_val), actual_ml, stype)
                row["actual_pnl_per_share"] = round(float(actual_pps), 4)
                row["actual_pnl_per_contract"] = round(float(actual_pps) * 100, 2)

        any_actual = False
        total_actual = 0.0
        for p in picks:
            tk = p.get("ticker")
            r = results.get(tk) or {}
            ap = r.get("actual_pnl_per_contract")
            if ap is not None:
                total_actual += float(ap)
                any_actual = True
            else:
                theo = r.get("pnl_per_contract")
                if theo is not None:
                    total_actual += float(theo)
        if any_actual:
            outcome["total_pnl_per_contract_actual"] = round(total_actual, 2)
        else:
            outcome.pop("total_pnl_per_contract_actual", None)

    _atomic_write_json(fp, payload)
    return jsonify({
        "ok": True,
        "actual_credit": target.get("actual_credit"),
        "actual_max_loss": target.get("actual_max_loss"),
    })


def main() -> int:
    # Env-var overrides for production (e.g., Mya bind 0.0.0.0:8080).
    host = os.environ.get("WEBAPP_HOST", live_config.WEBAPP_HOST)
    port = int(os.environ.get("WEBAPP_PORT", live_config.WEBAPP_PORT))
    app.run(host=host, port=port, debug=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
