"""Data lineage and coverage metadata for live snapshots and ranked outputs."""
from __future__ import annotations

import hashlib
import json
import math
import os
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

import config as backtest_config
from live import live_config


ET = ZoneInfo("America/New_York")
ROOT = Path(__file__).resolve().parents[1]


def manifest_path(snapshot_path: Path) -> Path:
    return snapshot_path.with_suffix(".manifest.json")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git_sha() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
            timeout=2,
        )
        return result.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def _config_value(value):
    if isinstance(value, float) and not math.isfinite(value):
        return repr(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (list, tuple)):
        return [_config_value(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _config_value(v) for k, v in sorted(value.items(), key=lambda x: str(x[0]))}
    return repr(value)


def config_hash() -> str:
    payload = {}
    for label, module in (("backtest", backtest_config), ("live", live_config)):
        payload[label] = {
            name: _config_value(value)
            for name, value in vars(module).items()
            if name.isupper() and not name.startswith("__")
        }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(encoded).hexdigest()


def _coverage(df: pd.DataFrame, column: str) -> float | None:
    if column not in df.columns or len(df) == 0:
        return None
    return round(float(df[column].notna().mean()), 6)


def snapshot_manifest(df: pd.DataFrame, snapshot_path: Path, source: str = "IBKR") -> dict:
    dates = pd.to_datetime(df["DataDate"], errors="coerce") if "DataDate" in df else pd.Series(dtype="datetime64[ns]")
    oi = pd.to_numeric(df["OpenInterest"], errors="coerce") if "OpenInterest" in df else pd.Series(dtype=float)
    volume = pd.to_numeric(df["Volume"], errors="coerce") if "Volume" in df else pd.Series(dtype=float)
    bbo = all(col in df.columns for col in ("BidSize", "AskSize"))
    bbo_ok = (pd.to_numeric(df["BidSize"], errors="coerce") > 0) & (pd.to_numeric(df["AskSize"], errors="coerce") > 0) if bbo else pd.Series(dtype=bool)
    now = datetime.now(ET)
    return {
        "schema_version": 1,
        "source": source,
        "snapshot_file": str(snapshot_path.relative_to(ROOT)) if snapshot_path.is_relative_to(ROOT) else str(snapshot_path),
        "created_at": now.isoformat(timespec="seconds"),
        "snapshot_mtime": datetime.fromtimestamp(snapshot_path.stat().st_mtime, ET).isoformat(timespec="seconds") if snapshot_path.exists() else None,
        "snapshot_sha256": file_sha256(snapshot_path) if snapshot_path.exists() else None,
        "git_sha": git_sha(),
        "config_hash": config_hash(),
        "row_count": int(len(df)),
        "ticker_count": int(df["Symbol"].nunique()) if "Symbol" in df else 0,
        "tickers": sorted(df["Symbol"].dropna().astype(str).unique().tolist()) if "Symbol" in df else [],
        "data_date_min": dates.min().date().isoformat() if dates.notna().any() else None,
        "data_date_max": dates.max().date().isoformat() if dates.notna().any() else None,
        "expiration_count": int(df["ExpirationDate"].nunique()) if "ExpirationDate" in df else 0,
        "put_rows": int((df["PutCall"].astype(str).str.lower() == "put").sum()) if "PutCall" in df else 0,
        "call_rows": int((df["PutCall"].astype(str).str.lower() == "call").sum()) if "PutCall" in df else 0,
        "coverage": {column: _coverage(df, column) for column in (
            "BidPrice", "AskPrice", "AbsDelta", "UnderlyingPrice",
            "ImpliedVolatility", "OpenInterest", "Volume", "BidSize", "AskSize",
        )},
        "liquidity": {
            "oi_positive_fraction": round(float((oi > 0).mean()), 6) if len(oi) else None,
            "volume_threshold_fraction": round(float((volume >= live_config.LIVE_MIN_VOLUME).mean()), 6) if len(volume) else None,
            "bbo_both_sides_fraction": round(float(bbo_ok.mean()), 6) if len(bbo_ok) else None,
            "volume_bbo_fallback_fraction": round(float(((oi < live_config.LIVE_MIN_OPEN_INTEREST) & (volume >= live_config.LIVE_MIN_VOLUME) & bbo_ok).mean()), 6) if len(oi) and len(volume) and len(bbo_ok) else None,
        },
    }


def write_manifest(manifest: dict, snapshot_path: Path) -> Path:
    out = manifest_path(snapshot_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=out.parent, prefix=".manifest-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(manifest, handle, indent=2, sort_keys=True)
        os.replace(tmp, out)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return out


def read_manifest(snapshot_path: Path) -> dict | None:
    path = manifest_path(snapshot_path)
    try:
        with path.open() as handle:
            payload = json.load(handle)
        return payload if isinstance(payload, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def ranked_provenance(snapshot_path: Path, ranked_rows: int, qualified_rows: int) -> dict:
    manifest = read_manifest(snapshot_path) or {}
    manifest["ranked_at"] = datetime.now(ET).isoformat(timespec="seconds")
    manifest["ranker_git_sha"] = git_sha()
    manifest["ranker_config_hash"] = config_hash()
    manifest["ranked_row_count"] = int(ranked_rows)
    manifest["qualified_row_count"] = int(qualified_rows)
    return manifest
