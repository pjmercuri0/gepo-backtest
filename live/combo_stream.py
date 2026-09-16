"""Always-on combo quote stream for the ranked board.

Why: combo quotes arrive ~2-3s after you subscribe, so a cold requote cannot be
done in a second (measured 2026-09-16: a 12-bag batch got 1/12 in 1.5s, 8/12 in
3.3s). Holding the subscriptions open removes the wait entirely -- ib_insync
updates the Ticker objects in place, so reading them is instant.

Loop:
  - re-read live/ranked/latest.json every RELOAD_S to learn the current board
  - resolve leg conIds from the snapshot parquet that payload names
  - subscribe to bags that are new, cancel bags that fell off
  - write live/ranked/combo_stream.json every WRITE_S

The webapp overlays that file onto /api/latest.json, so the tab shows quotes a
second old instead of up to a scan old. Selection is untouched: the ranker still
scores on the model credit and the file only carries quotes.

Read-only IBKR, client id 111 (110 is the batch combo fetch, 100-109 fetchers).
Idempotent: exits if another copy holds the lock.
"""
from __future__ import annotations
import json, os, sys, time
from datetime import datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ib_insync import IB
from live import live_config
from live.combo_quotes import _bag_for
from live.fetcher import _connect_with_retry

CLIENT_ID = int(getattr(live_config, "LIVE_STREAM_CLIENT_ID", 111))
MAX_BAGS  = int(getattr(live_config, "LIVE_STREAM_MAX_BAGS", 45))
RELOAD_S  = float(getattr(live_config, "LIVE_STREAM_RELOAD_S", 20))
WRITE_S   = float(getattr(live_config, "LIVE_STREAM_WRITE_S", 1.0))
OUT   = Path(live_config.RANKED_DIR) / "combo_stream.json"
PUBLISH_S = float(getattr(live_config, "LIVE_STREAM_PUBLISH_S", 2.0))
LOCK  = ROOT / "live" / "logs" / "combo_stream.lock"


def _key(r) -> str:
    return f"{r['ticker']}|{r['spread_type']}|{float(r['short_strike']):g}|{float(r['long_strike']):g}|{str(r['expiry_date'])[:10]}"


def _board() -> list[dict]:
    """Ranked rows with leg conIds resolved from the payload's own snapshot."""
    payload = json.loads((Path(live_config.RANKED_DIR) / "latest.json").read_text())
    rows = (payload.get("ticker") or [])[:MAX_BAGS]
    snap_path = ROOT / str(payload.get("snapshot_file") or "")
    if not snap_path.exists():
        return []
    snap = pd.read_parquet(snap_path, columns=["Symbol", "ExpirationDate", "StrikePrice", "PutCall", "conId"])
    snap["exp"] = pd.to_datetime(snap.ExpirationDate).dt.strftime("%Y-%m-%d")
    snap["pc"] = snap.PutCall.astype(str).str.lower()
    idx = {(r.Symbol, r.exp, float(r.StrikePrice), r.pc): int(r.conId) for r in snap.itertuples()}
    out = []
    for r in rows:
        exp = str(r.get("expiry_date"))[:10]
        pc = "put" if r.get("spread_type") == "bull_put" else "call"
        s = idx.get((r.get("ticker"), exp, float(r.get("short_strike")), pc))
        l = idx.get((r.get("ticker"), exp, float(r.get("long_strike")), pc))
        if s and l:
            out.append({**r, "short_conid": s, "long_conid": l, "_key": _key(r)})
    return out


def _atomic(path: Path, payload: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload))
    tmp.replace(path)


def _publish(last_at: float) -> float:
    """rsync the quote file to Mya. Mya has no IBKR, so the tab can only show live
    quotes if the mini pushes them. Uses an ssh ControlMaster socket so each push is
    a few tens of ms, not a fresh handshake."""
    host = os.environ.get("MYA_SSH_HOST")
    if not host or time.monotonic() - last_at < PUBLISH_S:
        return last_at
    base = os.environ.get("MYA_REMOTE_BASE", "/opt/vito/gepo-backtest/live")
    sock = "/tmp/gepo_combo_stream_%r@%h:%p"
    ssh = ("ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new "
           f"-o ControlMaster=auto -o ControlPath={sock} -o ControlPersist=300")
    os.system(f"rsync -az --timeout=5 -e '{ssh}' {OUT} {host}:{base}/ranked/ >/dev/null 2>&1 &")
    return time.monotonic()


def main() -> int:
    try:
        LOCK.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode()); os.close(fd)
    except FileExistsError:
        try:
            pid = int(LOCK.read_text().strip()); os.kill(pid, 0)
            print(f"[combo_stream] already running (pid {pid})", flush=True); return 0
        except (ValueError, ProcessLookupError, PermissionError):
            LOCK.unlink(missing_ok=True)
            fd = os.open(LOCK, os.O_CREAT | os.O_WRONLY); os.write(fd, str(os.getpid()).encode()); os.close(fd)

    ib = IB()
    subs: dict[str, tuple] = {}          # key -> (bag, ticker)
    try:
        _connect_with_retry(ib, CLIENT_ID)
        ib.reqMarketDataType(live_config.IB_MKT_DATA_TYPE)
        print(f"[combo_stream] connected, client {CLIENT_ID}, max {MAX_BAGS} bags", flush=True)
        last_reload = 0.0
        last_pub = 0.0
        while True:
            now = time.monotonic()
            if now - last_reload >= RELOAD_S:
                last_reload = now
                try:
                    board = _board()
                except Exception as e:
                    board = []
                    print(f"[combo_stream] board read failed: {e}", flush=True)
                want = {r["_key"]: r for r in board}
                for k in list(subs):
                    if k not in want:
                        bag, _t = subs.pop(k)
                        try: ib.cancelMktData(bag)
                        except Exception: pass
                for k, r in want.items():
                    if k in subs:
                        continue
                    bag = _bag_for(r)
                    if bag is None:
                        continue
                    try:
                        subs[k] = (bag, ib.reqMktData(bag, "", False, False))
                    except Exception as e:
                        print(f"[combo_stream] subscribe failed {k}: {e}", flush=True)
                print(f"[combo_stream] holding {len(subs)} subscriptions", flush=True)

            ib.sleep(WRITE_S)
            ts = datetime.now().isoformat(timespec="seconds")
            quotes = {}
            for k, (_bag, t) in subs.items():
                bid, ask, last = t.bid, t.ask, t.last
                ok = lambda v: v is not None and v == v and v != 0      # not None/NaN/0
                if not (ok(bid) and ok(ask)):
                    continue
                # Combo quotes come back negative for a credit; mid is the credit.
                quotes[k] = {"bid": float(bid), "ask": float(ask),
                             "mid": round(-(float(bid) + float(ask)) / 2.0, 4),
                             "last": (float(last) if ok(last) else None), "ts": ts}
            _atomic(OUT, {"ts": ts, "n": len(quotes), "held": len(subs), "quotes": quotes})
            last_pub = _publish(last_pub)
    except KeyboardInterrupt:
        pass
    finally:
        try:
            for _k, (bag, _t) in subs.items():
                try: ib.cancelMktData(bag)
                except Exception: pass
            if ib.isConnected():
                ib.disconnect()
        finally:
            LOCK.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
