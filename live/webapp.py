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
import os
import sys
from datetime import datetime, time as dtime
from pathlib import Path

from flask import Flask, jsonify, render_template, abort, send_from_directory

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from live import live_config
from live.regime import current_regime


app = Flask(
    __name__,
    template_folder=str(Path(__file__).parent / "templates"),
    static_folder=str(Path(__file__).parent / "static"),
)
# Pick up template + CSS edits on the next request without restarting Flask.
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.jinja_env.auto_reload = True
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0   # static files served with no-cache


# ── Helpers ────────────────────────────────────────────────────────────────

def _read_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


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


def _frozen_history(limit: int = 60) -> list[dict]:
    base = Path(live_config.FROZEN_DIR)
    if not base.exists():
        return []
    out = []
    for fp in sorted(base.glob("*.json"), reverse=True)[:limit]:
        payload = _read_json(fp)
        if payload is None:
            continue
        payload["date"] = fp.stem
        out.append(payload)
    return out


# ── Routes ─────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template(
        "index.html",
        poll_seconds=live_config.WEBAPP_POLL_SECONDS,
    )


@app.route("/history")
def history():
    return render_template("history.html", entries=_frozen_history())


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
            "error": "no ranked snapshot found yet — run the fetcher + ranker",
        })
    payload["frozen"] = False
    # Always re-evaluate regime against the freshest data on disk, so the
    # subheader matches the SPY chip even if `latest.json` was baked earlier.
    payload["regime"] = current_regime()
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


def main() -> int:
    # Env-var overrides for production (e.g., Mya bind 0.0.0.0:8080).
    host = os.environ.get("WEBAPP_HOST", live_config.WEBAPP_HOST)
    port = int(os.environ.get("WEBAPP_PORT", live_config.WEBAPP_PORT))
    app.run(host=host, port=port, debug=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
