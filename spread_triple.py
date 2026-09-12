"""52:10 canon (2026-09-12) — the empirical 3-state triple for DKL.

P_emp = (p WIN, q LOSS, ro PARTIAL), COUNTED directly from realized historical
spreads. Replaces the single-leg P(ITM) table, which derived
`ro = P(short ITM) - P(long ITM)` and forced ro=0 on 19.5% of delta-20
candidates because both legs fell in one 0.1-wide delta bucket.

Lookup, in order:
  1. (ticker, dollar_width_bucket)  — the name's OWN history. Primary.
  2. pooled (DTE, short_delta_bucket, width_bucket) — fallback ONLY when the
     ticker cell is empty, then coarser, then the window's global mix.

Per user direction 2026-09-12: pooling for everyone is undesirable because the
estimate then moves with the composition of the pool rather than with the name.
The pool is a data-sufficiency backstop, not the default.

Window: the last N_EXPIRIES weekly expiries STRICTLY BEFORE the as-of date
(N=52, ~1 year). Expiry-count, not calendar days: equities only list Friday
expiries, so a fixed day-count window admits 4 or 5 cohorts depending on the
entry weekday.

Population: output/spread_outcomes*.parquet, built by
build_spread_outcome_table.py (delta-20 spreads, gate OFF, Mon-Thu entries,
Friday expiry, DTE 1-4, OI>=100).
"""
import os
import numpy as np
import pandas as pd

N_EXPIRIES = 52
MIN_N = 30
DOLLAR_BINS = [0, 0.75, 1.75, 3.75, 1e9]      # ladder: 0.50 / 1.00 / 2.50 / 5.00
DELTA_MULT = 10
POP_PATHS = ['output/spread_outcomes.parquet', 'output/spread_outcomes_2026.parquet']

_POP = None            # full realized-spread population, sorted by expiry
_EXPIRIES = None       # sorted unique expiry dates
_TW = None             # installed (ticker, wb) table
_POOLED = None         # installed pooled fallback tables
_ASOF = None


def _bucket(df, delta_col):
    d = df.copy()
    d['width'] = (d['short_strike'] - d['long_strike']).abs()
    d['sdb'] = (d[delta_col].abs() * DELTA_MULT).astype(int).clip(0, DELTA_MULT - 1)
    d['wb'] = pd.cut(d['width'], bins=DOLLAR_BINS, labels=False, include_lowest=True)
    return d


def load_population(paths=None):
    """Load and cache the realized-spread population. Missing files are skipped."""
    global _POP, _EXPIRIES
    if _POP is not None:
        return _POP
    frames = []
    for p in (paths or POP_PATHS):
        if os.path.exists(p):
            frames.append(pd.read_parquet(p))
    if not frames:
        raise FileNotFoundError(
            'No spread-outcome population found. Run build_spread_outcome_table.py.')
    P = pd.concat(frames, ignore_index=True)
    P['entry_date'] = pd.to_datetime(P['entry_date'])
    P['expiry_date'] = pd.to_datetime(P['expiry_date'])
    P = _bucket(P, 'abs_short_delta')
    P = P.sort_values('expiry_date').reset_index(drop=True)
    _POP = P
    _EXPIRIES = np.sort(P['expiry_date'].unique())
    return _POP


def _rates(sub, keys):
    g = sub.groupby(keys)['outcome'].value_counts().unstack(fill_value=0)
    for c in ('WIN', 'PARTIAL', 'LOSS'):
        if c not in g.columns:
            g[c] = 0
    g['n'] = g[['WIN', 'PARTIAL', 'LOSS']].sum(axis=1)
    return g


def install_window(asof, n_expiries=N_EXPIRIES):
    """Build the ticker and pooled tables from the last n_expiries BEFORE asof.

    Strictly causal: an expiry on asof itself is excluded, so an outcome can
    never inform a trade entered before it realized."""
    global _TW, _POOLED, _ASOF
    P = load_population()
    asof = pd.Timestamp(asof)
    prior = _EXPIRIES[_EXPIRIES < asof.to_datetime64()]
    if len(prior) == 0:
        _TW = _POOLED = None
        return False
    sub = P[P['expiry_date'].isin(prior[-n_expiries:])]
    if sub.empty:
        _TW = _POOLED = None
        return False
    _TW = _rates(sub, ['ticker', 'wb'])
    _POOLED = {
        'full':  _rates(sub, ['DTE', 'sdb', 'wb']),
        'nodte': _rates(sub, ['sdb', 'wb']),
        'w':     _rates(sub, ['wb']),
    }
    gl = sub['outcome'].value_counts(); t = gl.sum()
    _POOLED['global'] = (gl.get('WIN', 0) / t, gl.get('LOSS', 0) / t, gl.get('PARTIAL', 0) / t)
    _ASOF = asof
    return True


def _norm(p, q, ro):
    s = p + q + ro
    if s <= 0:
        return None
    return p / s, q / s, ro / s


def lookup(ticker, dte, short_delta, width):
    """(p, q, ro, source). source is 'ticker' | 'full' | 'nodte' | 'w' | 'global'."""
    if _TW is None:
        return None, None, None, None
    wb = int(np.digitize([abs(width)], DOLLAR_BINS)[0] - 1)
    wb = min(max(wb, 0), len(DOLLAR_BINS) - 2)
    sdb = int(min(max(abs(short_delta) * DELTA_MULT, 0), DELTA_MULT - 1))
    dte_i = int(min(max(dte, 1), 4))

    # 1) the name's own history
    key = (ticker, wb)
    if key in _TW.index:
        row = _TW.loc[key]
        if isinstance(row, pd.DataFrame):
            row = row.iloc[0]
        n = float(row['n'])
        if n > 0:
            out = _norm(row['WIN'] / n, row['LOSS'] / n, row['PARTIAL'] / n)
            if out:
                return out[0], out[1], out[2], 'ticker'

    # 2) pooled fallback, finest key first
    for name, k in (('full', (dte_i, sdb, wb)), ('nodte', (sdb, wb)), ('w', (wb,))):
        try:
            row = _POOLED[name].loc[k]
        except (KeyError, TypeError):
            continue
        if isinstance(row, pd.DataFrame):
            row = row.iloc[0]
        n = float(row['n'])
        if n >= MIN_N:
            out = _norm(row['WIN'] / n, row['LOSS'] / n, row['PARTIAL'] / n)
            if out:
                return out[0], out[1], out[2], name
    p, q, ro = _POOLED['global']
    out = _norm(p, q, ro)
    if out is None:
        return None, None, None, None
    return out[0], out[1], out[2], 'global'


# ── Live path ───────────────────────────────────────────────────────────────
# The population is rebuilt from vendor year-parquets weekly (see
# refresh_spread_outcomes.py, wired into cron_pool_refresh.sh). The installed
# artifact is small and only changes when that file changes, so half-hourly
# cron firings reuse a cache instead of re-reading the population.

WINDOW_CACHE_PATH = 'output/spread_triple_window_cache.pkl'
# Vendor data lags real time; warn rather than fail, but make the lag visible.
MAX_STALE_DAYS = 45


def latest_expiry():
    load_population()
    return pd.Timestamp(_EXPIRIES[-1])


def install_latest_cached(n_expiries=N_EXPIRIES, today=None):
    """Install the most recent window for live scoring. Returns the as-of date.

    Cache key includes the population mtime AND the window spec, so changing
    N_EXPIRIES or rebuilding the population invalidates it. (The old
    empirical_runner cache keyed only on mtime+date, which would have served
    stale tables silently across a semantics change.)"""
    import pickle
    global _TW, _POOLED, _ASOF
    mt = max(os.path.getmtime(p) for p in POP_PATHS if os.path.exists(p))
    today = pd.Timestamp(today or pd.Timestamp.today().normalize())
    key = (mt, int(n_expiries), str(today.date()), DELTA_MULT, tuple(DOLLAR_BINS))
    try:
        with open(WINDOW_CACHE_PATH, 'rb') as f:
            c = pickle.load(f)
        if c.get('key') == key:
            _TW, _POOLED, _ASOF = c['tw'], c['pooled'], c['asof']
            return _ASOF
    except Exception:
        pass
    # asof = the day AFTER the newest expiry, so that expiry is included
    newest = latest_expiry()
    asof = max(today, newest + pd.Timedelta(days=1))
    if not install_window(asof, n_expiries):
        raise RuntimeError('spread_triple: could not build a window from the population')
    lag = (today - newest).days
    if lag > MAX_STALE_DAYS:
        print(f'[spread_triple] WARNING: population newest expiry {newest.date()} is '
              f'{lag} days stale (>{MAX_STALE_DAYS}). Run refresh_spread_outcomes.py.',
              flush=True)
    try:
        with open(WINDOW_CACHE_PATH, 'wb') as f:
            pickle.dump({'key': key, 'tw': _TW, 'pooled': _POOLED, 'asof': _ASOF}, f)
    except Exception:
        pass
    return _ASOF
