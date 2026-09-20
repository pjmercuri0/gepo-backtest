"""Fail-closed startup checks for the live pipeline.

The preflight is intentionally side-effect-light: it validates configuration,
local inputs, output paths, snapshot freshness/schema, and (for fetches) the
IB Gateway socket. It does not fetch data or publish output.
"""
from __future__ import annotations

import argparse
import os
import shutil
import socket
from dataclasses import dataclass
from datetime import datetime, time as dtime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from live import live_config
from live.provenance import manifest_path, read_manifest


ET = ZoneInfo("America/New_York")
ROOT = Path(__file__).resolve().parents[1]
REQUIRED_SNAPSHOT_COLUMNS = {
    "Symbol", "DataDate", "ExpirationDate", "StrikePrice", "PutCall",
    "BidPrice", "AskPrice", "AbsDelta", "UnderlyingPrice",
    "ImpliedVolatility", "OpenInterest",
}
LIQUIDITY_COLUMNS = {"Volume", "BidSize", "AskSize"}


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str


class PreflightError(RuntimeError):
    """Raised when a live run must be stopped before data is fetched/published."""

    def __init__(self, checks: list[Check]):
        self.checks = checks
        failures = "; ".join(f"{c.name}: {c.detail}" for c in checks if not c.ok)
        super().__init__(f"live preflight failed: {failures}")


def _now_et(now: datetime | None = None) -> datetime:
    if now is None:
        return datetime.now(ET)
    return now.astimezone(ET) if now.tzinfo else now.replace(tzinfo=ET)


def _market_window() -> tuple[dtime, dtime]:
    start = dtime.fromisoformat(live_config.FETCH_WINDOW_START)
    end = dtime.fromisoformat(live_config.FETCH_WINDOW_END)
    return start, end


def _check_config() -> list[Check]:
    checks = []
    port = int(live_config.IB_PORT)
    checks.append(Check("ib-port", 1 <= port <= 65535, f"{port}"))
    md_type = int(live_config.IB_MKT_DATA_TYPE)
    checks.append(Check("market-data-type", md_type in {1, 2, 3, 4}, f"{md_type}"))
    dte_min, dte_max = live_config.live_dte_window()
    checks.append(Check("dte-window", 0 <= dte_min <= dte_max <= 30,
                        f"[{dte_min}, {dte_max}]"))
    checks.append(Check(
        "liquidity-thresholds",
        live_config.LIVE_MIN_OPEN_INTEREST > 0
        and live_config.LIVE_MIN_VOLUME > 0
        and live_config.LIVE_MIN_BBO_SIZE > 0,
        f"OI>={live_config.LIVE_MIN_OPEN_INTEREST}, "
        f"volume>={live_config.LIVE_MIN_VOLUME}, "
        f"BBO>={live_config.LIVE_MIN_BBO_SIZE}",
    ))
    checks.append(Check(
        "feature-policy",
        bool(live_config.LIVE_FAIL_CLOSED_ON_MISSING_FEATURES)
        and bool(live_config.LIVE_REQUIRE_PARITY)
        and bool(live_config.LIVE_REQUIRE_IBKR_CLOSES)
        and bool(live_config.LIVE_REQUIRE_OWN_GAP),
        "missing-feature policy must be fail-closed for production",
    ))
    return checks


def _check_paths(mode: str) -> list[Check]:
    checks = []
    required = [ROOT / "data" / "spy_us_d.csv"]
    if mode == "rank":
        required.append(Path(
            os.environ.get("GEPO_IBKR_CLOSES", ROOT / "output" / "ibkr_closes.parquet")
        ))
    for path in required:
        try:
            label = str(path.relative_to(ROOT))
        except ValueError:
            label = str(path)
        checks.append(Check(f"required:{label}", path.is_file(), str(path)))

    output_dirs = [Path(live_config.RANKED_DIR), Path(live_config.LOGS_DIR)]
    if mode == "fetch":
        output_dirs += [Path(live_config.SNAPSHOTS_DIR), Path(live_config.CACHE_DIR)]
    for path in output_dirs:
        try:
            path.mkdir(parents=True, exist_ok=True)
            probe = path / ".preflight-write-test"
            probe.write_text("ok")
            probe.unlink()
            try:
                label = str(path.relative_to(ROOT))
            except ValueError:
                label = str(path)
            checks.append(Check(f"writable:{label}", True, "ok"))
        except OSError as exc:
            checks.append(Check(f"writable:{path}", False, str(exc)))

    usage = shutil.disk_usage(ROOT)
    checks.append(Check("disk-space", usage.free >= 1_000_000_000,
                        f"{usage.free / 1_000_000_000:.2f} GB free"))
    return checks


def _check_market_session(now: datetime, allow_out_of_hours: bool) -> list[Check]:
    start, end = _market_window()
    in_window = now.weekday() < 5 and start <= now.time() <= end
    return [Check(
        "market-session",
        in_window or allow_out_of_hours,
        f"{now.isoformat(timespec='minutes')} ET; window {start.isoformat()}-{end.isoformat()}"
        + ("; override enabled" if allow_out_of_hours else ""),
    )]


def _check_gateway() -> Check:
    try:
        with socket.create_connection((live_config.IB_HOST, live_config.IB_PORT), timeout=2):
            return Check("ib-gateway", True, f"{live_config.IB_HOST}:{live_config.IB_PORT}")
    except OSError as exc:
        return Check("ib-gateway", False, f"{live_config.IB_HOST}:{live_config.IB_PORT}: {exc}")


def _check_snapshot(path: Path, now: datetime, allow_stale: bool) -> list[Check]:
    checks = []
    if not path.is_file():
        return [Check("snapshot-file", False, f"missing: {path}")]
    checks.append(Check("snapshot-file", path.stat().st_size > 0, str(path)))
    file_time = datetime.fromtimestamp(path.stat().st_mtime, tz=ET)
    age = now - file_time
    checks.append(Check("snapshot-age", allow_stale or age <= timedelta(minutes=30),
                        f"{age.total_seconds() / 60:.1f} minutes"))
    try:
        df = pd.read_parquet(path)
        missing = REQUIRED_SNAPSHOT_COLUMNS - set(df.columns)
        checks.append(Check("snapshot-schema", not missing,
                            "missing=" + ",".join(sorted(missing)) if missing else "ok"))
        if live_config.LIVE_ALLOW_VOLUME_FALLBACK:
            missing_liq = LIQUIDITY_COLUMNS - set(df.columns)
            checks.append(Check("snapshot-liquidity-schema", not missing_liq,
                                "missing=" + ",".join(sorted(missing_liq)) if missing_liq else "ok"))
        if "DataDate" in df.columns and len(df):
            dates = pd.to_datetime(df["DataDate"], errors="coerce").dt.date.dropna()
            checks.append(Check("snapshot-date", bool(len(dates)) and dates.max() == now.date(),
                                f"latest={dates.max() if len(dates) else None}, expected={now.date()}"))
        checks.append(Check("snapshot-rows", len(df) > 0, f"{len(df):,} rows"))
        if live_config.LIVE_REQUIRE_SNAPSHOT_MANIFEST:
            manifest = read_manifest(path)
            checks.append(Check("snapshot-manifest", manifest is not None, str(manifest_path(path))))
            if manifest is not None:
                checks.append(Check("manifest-row-count", manifest.get("row_count") == len(df),
                                    f"manifest={manifest.get('row_count')}, actual={len(df)}"))
                # No hash gating (user, 2026-09-20). config_hash and
                # snapshot_sha256 are still RECORDED in the manifest for
                # forensics, but a mismatch no longer blocks a scan: the only
                # thing it caught was a config edit between fetch and rank, and
                # it cost a blocked run. Row count is kept -- that one catches a
                # genuinely truncated merge.
    except Exception as exc:
        checks.append(Check("snapshot-readable", False, str(exc)))
    return checks


def run(
    mode: str,
    snapshot: Path | None = None,
    *,
    now: datetime | None = None,
    allow_out_of_hours: bool = False,
    allow_stale_snapshot: bool = False,
) -> dict:
    """Run preflight checks and raise :class:`PreflightError` on failure."""
    if mode not in {"fetch", "rank"}:
        raise ValueError(f"unsupported preflight mode: {mode}")
    current = _now_et(now)
    checks = _check_config()
    checks += _check_market_session(current, allow_out_of_hours)
    checks += _check_paths(mode)
    if mode == "fetch":
        checks.append(_check_gateway())
    if mode == "rank" and snapshot is not None:
        checks += _check_snapshot(snapshot, current, allow_stale_snapshot)
    report = {
        "mode": mode,
        "timestamp": current.isoformat(timespec="seconds"),
        "checks": [c.__dict__ for c in checks],
        "ok": all(c.ok for c in checks),
    }
    for check in checks:
        print(f"  {'PASS' if check.ok else 'FAIL'} preflight/{check.name}: {check.detail}", flush=True)
    if not report["ok"]:
        raise PreflightError(checks)
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["fetch", "rank"])
    parser.add_argument("--snapshot", type=Path)
    parser.add_argument("--allow-out-of-hours", action="store_true")
    parser.add_argument("--allow-stale-snapshot", action="store_true")
    args = parser.parse_args()
    try:
        run(args.mode, args.snapshot,
            allow_out_of_hours=args.allow_out_of_hours,
            allow_stale_snapshot=args.allow_stale_snapshot)
    except PreflightError as exc:
        print(str(exc), flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
