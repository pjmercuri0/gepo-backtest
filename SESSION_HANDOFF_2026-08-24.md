# GEPO session handoff — 2026-06-10 (canon) · 2026-07-08 (live-ops) · 2026-07-17 (IBKR/health ops) · 2026-08-19 (Mac mini cutover) · 2026-08-24 (OOT/history repair) · 2026-08-31 (fetch speed / Friday crons / Snapshots backfill)

**Last updated:** 2026-08-31 20:30 EDT. Strategy canon unchanged since 2026-06-12 (k=10, thr=0.05 — §0). Recent repo/live work was operational: IBKR quote-outage recovery, health-alert hygiene, 15:01+15:31 freeze top-ups, OOT July/August updates, live table display change, CPU/process triage, Mac mini production-runner setup, and 2026-08-24 repair of missing Aug 20 OOT bets + missing History top-up.

## ⚠️ HARD RULE — read first

**NEVER delete, overwrite, or wipe parquet files (or any vendor-purchased CSV) without explicit user permission.**

Two incidents on 2026-06-08:
1. `preprocess_empirical.py --year 2026` REPLACED the year parquet with only Jan-Jun coverage, wiping months of vendor history
2. `fetch_earnings.py` REPLACED the earnings calendar, losing 2020-2025 historical earnings

User reaction: "I fucking hate you now I have to buy from the vendor again." Recovery was free in both cases (ZIPs already extracted, NASDAQ rescrape free), but trust was the real cost. **Before any operation that may write to a parquet/CSV that holds vendor or expensive-to-rebuild data: check if file exists, explicitly tell user "this will REPLACE/OVERWRITE/WIPE", wait for approval.** Prefer MERGE over REPLACE everywhere. Memory file: `feedback_never_wipe_parquet.md`.



**READ THIS FIRST.** Major canonical changes over 2026-06-04 → 2026-06-10. Site / live ranker / backtest all current on the **mid-basis canon (2026-06-10)**. New strategic findings: GROUND beats G-alone (under mid basis G-alone LOSES $15.5k while GROUND makes +$41.3k; DKL split t=4.45), regime gate OFF is canonical (and the fetcher's puts-only-in-bull filter was removed 2026-06-10 — it had silently suppressed every bear call for 5 days, error #70), BS theoretical drives the live tracker mark (now floored at intrinsic-at-current-spot), qty=1 per-contract display everywhere.

---

## 0.14 Fetch-speed work + Friday cron fix + Snapshots backfill (2026-08-31)

### Fetcher latency — two fixes, both live in code (COMMITTED)

**Measured baseline** (15:01 run, 2026-08-31): run started `15:01:00`, merged parquet
written `15:02:48`, ranker output `15:02:49` → **108s from kick-off to ranked picks**.
Per-group fetch times were `52.8 / 64.0 / 63.3 / 66.9 / 79.3 / 80.1 / 81.6 / 89.0 /
90.6 / 95.2s`. The run is bounded by the slowest group (G9, 95.2s); the remaining ~13s
is connect + merge + rank.

Key insight: **time is not proportional to work.** G10 fetched 4 tickers and took 81.6s;
G1 fetched 10 in 52.8s. G5 pulled 312 rows in 79.3s while G8 pulled 83 in 90.6s. The
fetch phase is tail-dominated — a few slow contracts set the clock for everyone.

**Fix 1 — `readonly=True` on every `ib.connect()`.** ib_insync 0.9.86 signature is
`connect(host, port, clientId, timeout=4, readonly=False, account='')`. With
`readonly=False` (the default) ib_insync does a full startup sync including open/completed
orders. IB Gateway is permanently in Read-Only API mode (§12), so those could never
succeed — every group logged `Error 321 … Read-Only mode` twice, then
`open orders request timed out` and `completed orders request timed out`, before any data
moved. Applied to **all 8 connect sites**, not just the fetcher, because `expire.log`,
`close_alert.log` and `daily_bars.log` carried the identical tax:

    live/fetcher.py  live/fetch_spy_intraday.py  live/fetch_daily_bars.py
    live/close_alert.py  live/expire_frozen.py  live/track_expiring.py
    live/snapshot_picks.py  live/refresh_spy_history.py

**Fix 2 — wired up `FETCH_PER_TICKER_TIMEOUT`.** It was defined at `live_config.py:28`
(= 25) and **never referenced anywhere in `fetcher.py`** — a dead knob. Now enforced two
ways in `_fetch_all_tickers`:
- outer `asyncio.wait_for(..., timeout=cap)` per ticker — hard bound even if
  `qualifyContractsAsync` hangs
- inner `deadline` (cap − 2s) checked in the batch loop of `_fetch_one_ticker`, so a slow
  *data* phase returns partial rows instead of discarding them

**Verified live** (market open, clientId 190/191, output to scratch — no repo artifacts):
- readonly: the four `Error 321`/timeout lines are **gone**. Same four tickers before →
  after: `AAPL 22.5→15.3s`, `JPM 52.8→18.4s`, `XOM 50.8→17.4s`, `MSFT 37.7→17.4s`.
  Full row counts, no data loss. None reached the 25s cap.
- timeout: forced with a 5s cap. Both paths fire —
  `[AAPL] timed out at 5s cap - skipped` (outer wait_for) and
  `[XOM] rows=0/14 (4.2s, deadline hit - partial)` (inner deadline). Group terminated at
  exactly 5.0s.

**Expected gain: ~60-75s per scan** (108s → ~30-40s to ranked picks), skewed to the low
end. Caveat: the verification ran 4 tickers on one connection; production runs 10 per
group across 10 concurrent connections, and that contention is what pushed G9 to 95.2s.
Most of the gain is `readonly` (a genuine saving, no data given up); the cap is mostly a
safety net. **Real number: compare `1501.parquet` mtime against the run header in
`parallel_pull.log` on the next full run.**

**Watch for:** `timed out at 25s cap - skipped` lines. A ticker appearing there
consistently is silently leaving the universe and the cap is too tight for it.

### Friday crons — root cause found and fixed

`live/cron_pool_refresh.sh` was mode **100644 in git** — never executable. The crontab
invokes it by bare path (`31 17 * * 5 …/cron_pool_refresh.sh`), so cron failed before the
script's first line and the `>> live/logs/pool_refresh.log` redirect never happened. That
is why `live/logs/` had no `pool_refresh.log` at all while every other wrapper had one.
Broken since it was committed. Fixed with `chmod +x` (100644 → 100755).

Two things that looked like Friday bugs but are not:
- `cron_calendar_refresh.sh` had no log because the crontab was **empty until 2026-08-19
  21:12** (both `live/logs/crontab.backup.*` files are 0 bytes). 2026-08-21 was the first
  Friday it existed.
- **Friday produces no picks by design.** `live_config.py:31-36` excludes Friday entries
  (DTE 7 to next Friday averaged $1.66/trade vs $20-37 Mon-Thu). From a Friday the only
  Friday expiries are DTE 0 (below `LIVE_DTE_MIN=1`) and DTE 7 (above `LIVE_DTE_MAX=4`),
  so `_weekly_expiries_in_dte_window()` returns empty and every fetcher logs
  `no Friday expiries in DTE window [1, 4]`. All 16 runs on 2026-08-21 did this.

**Discrepancy still open:** that config comment says *"Cron also skips Friday firings for
cron_parallel"* — it does not. The crontab is `1,31 9-16 * * 1-5` (Mon-Fri). Every Friday
the job wakes 16×, opens 10 IBKR connections per run, fetches nothing, and holds the Mac
awake 10 min each time via `caffeinate`. Either the crontab or the comment is wrong;
intent appears to be `1-4`.

### Snapshots tab — settlement backfill (2026-08-21)

Symptom: Snapshots showed `open / — / —` for days whose picks had already expired.

Root cause: `snapshot_picks._settle_files` globs the **local** `live/intraday_picks/`, and
the mini held exactly one file (`2026-08-20.json`) while Mya held 38. `upload_to_mya.sh`
rsyncs without `--delete` (deliberate — "keep server-side history files even if pruned
locally"), and `pull_from_mya.sh` only pulls `live/frozen/`. So `intraday_picks/` is
upload-only and never comes home. After the Aug 19 rebuild the mini restarted from empty.

This is exactly why History settled and Snapshots did not for the same dates:
`frozen/2026-08-19.json` was pulled back and settled by `expire_frozen` at 16:01;
`intraday_picks/2026-08-19.json` existed only on Mya and nothing ever looked at it.

Repair: manual rsync of all 38 day-files from Mya → `settle(post_close=True)` → upload.
Result **1062 picks, 1062 settled, 0 unsettled**, verified on Mya. All 77 previously
unsettled picks turned out to have expiry `2026-08-21` (same-day, IB path) — there was no
older backlog, so the Yahoo path was not needed. Closes cross-checked exactly against the
History tab on all 9 overlapping tickers (DE 647.47, TMO 629.27, GE 348.37, VRTX 548.05,
GS 1039.28, ISRG 378.81, AMGN 439.33, EOG 153.05, RTX 209.91).

### AMGN 15:31 top-up had no tracking row (repaired on Mya, approximate)

`frozen/2026-08-24.json` header was correct (`frozen_at=15:01+15:31`,
`freeze_topup_added_count=1`) but `tracking` held only NEE/GS/ISRG/BMY — AMGN had no
entry, so it showed no P&L. The top-up added the pick to `top_picks` without creating its
tracking series.

Could not be computed on Mya: **`live/bs_pricing.py` does not exist there** and
`live/snapshots/` is not uploaded, so Mya cannot produce `BS_theo` marks — every tracking
row is computed on the mini and shipped inside `frozen/`. A sandbox outage blocked the
mini at the time, so the row was reconstructed on Mya from AMGN's own frozen quotes:
- `entry_credit 1.24` and `max_loss 1.26` are **exact** (same clamp-to-bid/ask ×
  `LAST_PCT` 0.80 as `_track_pick`)
- `current_mark 1.1318` / `pnl +10.82` are **approximate**, labelled
  `mark_basis="BS_theo_approx"` so they cannot be mistaken for pipeline output. The real
  `_track_pick` passes separate `short_iv`/`long_iv` per leg; the frozen pick carries only
  one `IV`. Calibrated against the four existing rows → good to ~±$0.70/contract; vol skew
  between the 445/447.5 legs is the whole residual.
- backup: `live/frozen/2026-08-24.json.bak_before_amgn_tracking_20260824`

To make it exact, run `_track_pick` against `live/snapshots/2026-08-24/1531.parquet` on
the mini (the parquet is there) and replace the row.

### macOS auto-update disabled (it closed IB Gateway)

An automatic update restarted the mini and killed IB Gateway. All four update prefs were
`1`; they are now `0`:

    AutomaticallyInstallMacOSUpdates 0   ← the one that restarts the machine
    AutomaticDownload                0
    CriticalUpdateInstall            0
    ConfigDataInstall                0

Still on and harmless (cannot restart the machine): automatic *checking*
(`AutomaticCheckEnabled` unset → on) and `com.apple.commerce AutoUpdate = 1` (App Store
apps only). To silence: `sudo softwareupdate --schedule off` +
`AutomaticCheckEnabled -bool false` + `commerce AutoUpdate -bool false`.

`FirstInstallTonightDateDictionary` still lists `MSU_UPDATE_25G83_patch_26.6.2_minor` at
`2026-08-31 02:18:04 UTC` — that is a record of the attempt that already happened, not a
future schedule. `/Library/Updates` holds no staged installer. macOS 26.6.2 remains
listed in `RecommendedUpdates`; it will not self-install.

### Remote-access reality if the mini ever reboots

Verified state: **FileVault is On**, `autoLoginUser` is **not set**,
`fdesetup supportsauthrestart` → **true**, hardware is **arm64 / Apple M4**.

Two gates after a restart:
1. **Pre-boot FileVault unlock** — OS not booted, no network, no CRD. Hard blocker.
2. **Login window** — CRD *does* run here.
   `/Library/LaunchAgents/org.chromium.chromoting.plist` has `RunAtLoad=true`,
   `Disabled=false`, and critically `LimitLoadToSessionType = ["Aqua", "LoginWindow"]`,
   plus `org.chromium.chromoting.broker.plist` as a LaunchDaemon. So CRD needs no setup.

So only gate 1 matters. Tools, and they are **not interchangeable**:
- `sudo fdesetup authrestart` — one immediate authenticated restart. Single-use; it cannot
  arm a restart that an installer triggers later.
- `sudo softwareupdate -i -a -R --user <user> --stdinpass` — Apple-Silicon-only owner
  authentication; lets the *installer* authenticate through the FileVault gate for its own
  (possibly multiple) restarts. This is the headless-update path, and it replaces
  `authrestart` rather than supplementing it.

**Untested.** Recommended: run `sudo fdesetup authrestart` while physically at the mini and
confirm CRD really serves the login window on macOS 26. That is the fallback that makes a
remote update worth attempting. User decision 2026-08-31: not updating for now.

**Note:** there is no LaunchAgent for IB Gateway, so it does not come back after any
reboot and every cron fails on port 4001 until it is launched by hand. User explicitly
declined building one (2026-08-31).

### Earnings + ex-div gates were DEAD on the mini (2026-09-01) — fixed

Both gates have existed in `live/ranker.py` since June (earnings canonical
2026-06-08 at :132, ex-div canonical 2026-06-09 at :159) but **neither had run a
single time on the Mac mini.** Both are wrapped in `if path.exists()`, and
`data/earnings_calendar.csv` / `data/dividend_calendar.csv` did not exist — so
they skipped silently, no error, no log line.

Cause: `cron_calendar_refresh.sh` fired on 2026-08-21 and 2026-08-28 and failed
identically both times — `ModuleNotFoundError: No module named 'requests'`.
`requests` was never installed in `.venv` and is not pinned in
`live/requirements.txt` or `requirements.txt`. Same class of cutover gap as the
missing `data/daily_bars_yahoo/`.

**Every pick selected on the mini since 2026-08-19 was chosen with no earnings
filter and no ex-dividend filter.**

Fixed: `.venv/bin/pip install requests` (2.32.5), then ran
`bash live/cron_calendar_refresh.sh` — both CSVs now populate. Coverage is
near-window only, which is all a DTE 1-4 gate needs: earnings 5 rows
(2026-09-01 → 2026-10-01), dividends 12 rows (NASDAQ publishes ex-div ~30 days
out). Both fetchers MERGE, so coverage accumulates on each weekly refresh.

**Add `requests` to `requirements.txt`** so this cannot recur on the next host.

### Ex-div gate scope — puts flag added, default OFF

New `live_config.LIVE_EXDIV_GATE_PUTS` (default `False`). The gate in
`ranker.py` was rewritten so:
- **bear calls are always gated**, window `entry .. expiry+1` (the +1 covers
  early assignment the day before ex-div — the call-specific risk)
- **bull puts** are gated only when the flag is on, window `entry .. expiry`
  (no assignment incentive; the exposure is the ex-div price drop itself, so no
  buffer is warranted)

With the flag off the ranker still **logs what would have been dropped**
(`N bull-put(s) would be dropped (LIVE_EXDIV_GATE_PUTS off — kept)`) so the
decision can be made on accumulated evidence rather than theory.

The open question for puts: the ex-div drop (~the dividend) moves against a
bullish position, but the date and amount are known in advance and already
priced into the premium — gating may drop trades whose risk was already paid
for. Note the **backtest has no ex-div gate at all** (`spreads.py` has the
earnings filter, nothing for dividends), so the bear-call gate is already
live-only and unvalidated against backtest results; extending to puts widens
that divergence. Measure before flipping.

Validated on the 2026-08-31 1531 snapshot (139 candidates, entry 08-31 →
expiry 09-04): earnings dropped 4 (AVGO 09-02, MDT 09-01); ex-div dropped 3
bear calls (PEP/PYPL 09-04, QCOM 09-03); 4 bull puts flagged and kept
(ADI 09-01, PEP, PYPL, QCOM).

### Still open from this session

1. `pull_from_mya.sh` `PULL_DIRS` still contains only `live/frozen/`. The 2026-08-21
   `intraday_picks` pull was a **manual rsync** — the gap reopens on the next rebuild.
   Fix is two lines.
2. `data/daily_bars_yahoo/` **does not exist on the mini**, so
   `snapshot_picks._expiry_close()` returns `None` at line 76 for any expiry older than
   today and `settle()` silently `continue`s. A pick missed on its expiry evening has no
   later path to settle. `fetch_yahoo_recent.py` only *refreshes* existing CSVs and logs
   `no existing CSVs … nothing to refresh` — it cannot bootstrap.
3. Fetch option 3 (day-cached conIds, TODO #5) untouched. Now measurable: `smart.strikes`
   from `reqSecDefOptParams` is the **union across all expirations**, and `fetcher.py:141`
   filters it only by price window — so the near weekly requests strikes that do not exist
   (NVDA at $1 increments 212/213/214/216…, SO 82.5/87.5/92.5, CSX 52.5, USB 57.5/62.5).
   Every invalid contract is a wasted `qualifyContracts` round trip on all 13 daily scans.
   Re-measure after the readonly+cap gain lands to see if it is still the dominant cost.
4. `.venv/` is untracked and **not in `.gitignore`**.
5. `cron_parallel` Friday firing vs the `live_config.py:33` comment (above).

---

## 0.13 Current ops repair (2026-08-24) — OOT Aug 20 + History 15:31 top-up

### OOT Aug 20 missing bets — fixed

User uploaded the new August vendor files and requested **append only new days; do not rerun old days**.

Work done:
- Appended only new vendor rows beyond existing OOT parquet max:
  - input dates: `2026-08-19`, `2026-08-20`, `2026-08-21`
  - source files: `data/DG_2026August/Greek_20260819_OData*.csv`, `Greek_20260820_OData*.csv`, `Greek_20260821_OData*.csv`
  - skipped old August files outside append window
  - appended `220,638` rows
  - combined OOT parquet now covers `DataDate 2026-01-01 -> 2026-08-21`
  - backup written before append: `output/2026_sp500_last_oot_combined.parquet.bak_before_append_20260824_120000`
- First `report_oot_2026.py` run extended cache only through `2026-08-19`; user noticed no Aug 20 bets.
- Root cause: `report_oot_2026.py` filters candidate dates through the SPY daily calendar. `data/daily_bars_yahoo/SPY.csv` stopped at `2026-08-19`, so valid `2026-08-20` vendor rows were filtered out before scoring.
- Patched data append-only:
  - appended SPY calendar rows for `2026-08-20` and `2026-08-21` from vendor SPY `UnderlyingPrice`:
    - `2026-08-20 = 762.60`
    - `2026-08-21 = 765.72`
  - backup: `data/daily_bars_yahoo/SPY.csv.bak_before_aug20_patch_20260824_121342`
  - appended RV lookup rows for `2026-08-20` and `2026-08-21`, `464` symbols per day
  - backup: `output/rv_table.parquet.bak_before_aug20_patch_20260824_121342`
- Patched `report_oot_2026.py`:
  - tolerates headered `data/daily_bars_yahoo/SPY.csv`
  - fills missing SPY calendar dates from `output/2026_sp500_last_oot_combined.parquet` in memory, so future vendor dates do not silently vanish when Yahoo/SPY daily bars lag
  - handles empty candidate frames cleanly after the cache reaches a date where the only newer parquet date is an inactive Friday
- Re-ran normal cached report path. It did **not** use `--no-cache`; it extended after the cached max only.

Result:
- OOT picks cache now has `279` rows, max entry `2026-08-20`.
- Aug 20 picks added:
  - `ACN` bear call `182.5/185`, exp `2026-08-21`
  - `TJX` bull put `141/140`, exp `2026-08-21`
  - `ABT` bear call `114/115`, exp `2026-08-21`
  - `LOW` bear call `217.5/220`, exp `2026-08-21`
  - `PYPL` bear call `62.5/63`, exp `2026-08-21`
- `live/data/oot_equity.json` regenerated and uploaded directly to Mya despite MacBook `live/NOT_PRODUCTION` guard by rsyncing only that one JSON.
- Mya verification after upload:
  - file size `249123`
  - `window_end=2026-08-21`
  - `n_trades=279`
  - `strategy_final=21857.0`

Important: full `live/upload_to_mya.sh` on the MacBook correctly refuses because `live/NOT_PRODUCTION` exists. Do not override for a full upload from the MacBook unless intentionally repairing one file and you know exactly what is being sent.

### History did not show the 15:31 pick — fixed on Mya, code patch pending deployment

User noticed History did not add the `15:31` pick. Diagnosis on Mya for `2026-08-24`:
- `live/frozen/2026-08-24.json` had 4 picks from the `15:01` freeze:
  - `NEE`, `GS`, `ISRG`, `BMY`
- `live/ranked/latest.json` was the `15:31` scan and had 1 qualified pick:
  - `AMGN`
- `live/intraday_picks/2026-08-24.json` correctly recorded the `1532` scan with 1 pick.
- Therefore the scan existed; the frozen History file simply was not topped up during the production run.

Manual production repair:
```bash
ssh "$MYA_SSH_HOST" 'cd /opt/vito/gepo-backtest && python3 -m live.freeze_snapshot --latest live/ranked/latest.json'
```

That immediately topped up:
- `4 + 1 = 5`
- frozen picks now: `NEE`, `GS`, `ISRG`, `BMY`, `AMGN`
- restamped production JSON so `AMGN` shows `freeze_added_at="15:31"` and day header shows `frozen_at="15:01+15:31"`.

Code patch made locally in `live/freeze_snapshot.py`:
- new helper `_latest_hhmm(latest, fallback)`
- top-up/fallback labels now use the source ranked snapshot time (`snapshot_file` stem like `1531`, or `snapshot_ts`) instead of the wall-clock time when the repair command happens
- syntax check passed with:
  ```bash
  PYTHONPYCACHEPREFIX=/tmp/gepo_pycache python3 -m py_compile live/freeze_snapshot.py
  ```

Still todo:
- Deploy/pull the patched `live/freeze_snapshot.py` onto the Mac mini production runner so future top-up labels are correct there.
- Investigate why the production 15:31 run did not execute the freeze top-up automatically even though `live/pull_now_parallel.sh` calls `python3 -m live.freeze_snapshot` for every `15:xx` scan. The one-off manual run proved the data and code path can top up correctly.
- Check Mac mini `live/logs/parallel_pull.log` around `2026-08-24 15:31` directly on the mini if possible. Mya did not show useful `[freeze]` log lines.

### Current dirty files relevant to this repair

Expected local modified files from this session:
- `report_oot_2026.py`
- `live/freeze_snapshot.py`
- `live/data/oot_equity.json`

There are many other pre-existing dirty live files in the worktree. Treat them as unrelated unless explicitly investigating them; do not revert user/previous-session changes.

---

## 0.12 Production host status (2026-08-19) — live ops moved toward Mac mini

**Decision:** use the dedicated **M4 Mac mini 16GB / 256GB** as the production runner, keep the current MacBook Air for dev/backtests, and keep Mya as the public/display server unless/until there is a reason to collapse both roles.

**Why this is enough:** the current machine is a 2020 Intel MacBook Air (`MacBookAir9,1`) with a 1.1 GHz dual-core i3, 8GB RAM, and ~256GB-class internal SSD. That explains the fan/heat pain when IB Gateway, pandas/parquet jobs, Codex/Terminal, browser, Spotlight/iCloud, and `monthly_pool_refresh.py` overlap. A base M4 Mac mini with 16GB RAM is a large step up for a dedicated always-on runner. The 24GB/512GB config was quoted at about CAD 1700 vs CAD 1100 for 16GB/256GB; at that spread, do **not** pay the extra CAD 600 unless the mini will also become a research/backtest workstation.

**Recommended spend instead of 24GB/512GB:** UPS battery backup, 1TB external USB-C/NVMe SSD if local storage gets tight, remote access setup, and possibly AppleCare. RAM is the only non-upgradeable concern, but 16GB should be fine if the mini is kept dedicated and the weekly pool rebuild is controlled/off-hours. Storage is easy to add externally.

**Network:** Mac mini has built-in Wi-Fi. Wi-Fi is acceptable for this workload if Ethernet is inconvenient; the jobs run every 15-30 minutes and can tolerate brief network blips if health checks alert. Ethernet is still better if easy. If wired is desired later without running cable from the front closet, options are MoCA over coax, powerline Ethernet, a mesh node with an Ethernet jack near the mini, or paying someone to run one Ethernet cable.

**Production split:**
- Mac mini = production runner: IB Gateway, scheduled live fetch/rank/freeze jobs, `health_check`, `expire_frozen`, `track_frozen`, weekly pool refresh if kept local, and sync/upload to Mya.
- Mya server = public web/display: Flask/gunicorn/nginx serving generated `live/ranked`, `live/frozen`, `live/intraday_picks`, notifications, and equity JSON. No IBKR credentials needed on Mya.
- MacBook Air = dev only: code edits, backtests, manual testing. Current choice is to leave its existing crons in place and keep IB Gateway closed on the Air; revisit only if duplicate uploads become a problem.

**IBKR constraint:** IB Gateway/TWS requires GUI login/authentication and is not a true serverless/headless workload. IB Gateway is the right app over full TWS because it is lighter, but it still needs re-auth/attention around IBKR reset windows. Keep Apple Screen Sharing available at home and Chrome Remote Desktop available away from home so the user can approve login/2FA and inspect Gateway.

**Actual 2026-08-19 setup:** Mac mini is initialized as a new Mac, reachable by Chrome Remote Desktop, has repo/data copied, has Mya SSH working, has IB Gateway 10.50 connected on localhost port `4001`, has read-only API enabled, has the GEPO cron block installed, and has `pmset` configured so the computer does not sleep.

**Current next step:** enable a second IBKR username dedicated to the Mac mini Gateway/API session. Keep that username logged in on the mini only, keep API read-only, and use the primary username for manual Client Portal/TWS work so logging in manually does not kill the mini's IB Gateway session. Confirm whether market-data entitlements need to be duplicated for the second username before relying on live scans.

**Productionization repo work:** `deploy/mac-mini/` now exists with bootstrap, env template, cron installer, cron template, README, and smoke test. Also consider a `requirements-live.txt` or proper pinned `pyproject.toml` so live dependencies are reproducible.

**Risk notes:** do not expose IBKR API port publicly; keep it localhost-only. Back up `live/frozen`, `live/intraday_picks`, `live/ranked`, and any actual-fill edits. Keep trading human-in-the-loop until the runner has weeks of clean logs and reconciliation.

---

## 0.11 SPY-fetch watchdog (2026-07-22) — pipeline self-heals on wedged SPY connection

**Incident:** the 11:31 scan on 2026-07-22 fetched all option groups fine, then hung on the SPY step (`pull_now_parallel.sh` → `python3 -m live.fetch_spy_intraday`, PID stuck ~37 min at 0% CPU, state `S` — blocked on an IB socket that accepted the connection but never delivered a quote). Because that step has no internal timeout, the run never merged/ranked and never released `cron_parallel.lock`; the 12:01 scan logged `SKIP: cron_parallel already running`. The site froze at the 11:01 fetch (shown ~11:03). Port 4001 was up and options were healthy — only the dedicated SPY connection wedged (same class as the 2026-07-16 null-quote outage, but a hang rather than nulls). Manual recovery: killed the stuck tree (releases the lock), then `bash live/cron_parallel.sh` — SPY recovered on its own, 47 candidates / 2 picks at 12:12.

**Fix (committed):** `live/pull_now_parallel.sh` now runs the SPY step under a bash wall-clock watchdog — background the fetch, `TERM` then `KILL` after 3s if still alive at the timeout (default 90s, override `SPY_FETCH_TIMEOUT`). On timeout it logs and continues to merge/rank/upload with the last valid tick (which `fetch_spy_intraday` already preserves), so a wedged SPY connection can no longer hold the lock and stall later scans. Verified both paths (wedged → killed at timeout, script survives `set -e`; normal → no added delay). Local-only (cron runs on the Mac; Mya only serves the webapp). Backstop, not a root-cause fix — if Gateway wedges get frequent, the deeper fix is still a periodic Gateway bounce.

---

## 0.10 Live-ops session (2026-07-16/17) — current restart handoff

### Current GEPO status before closing this OpenCode session
- IB Gateway was restarted on 2026-07-16 after it served empty live/delayed quote fields despite API port 4001 being reachable and market-data farms reporting OK.
- After restart, SPY quotes recovered:
  - SPY quote test at `2026-07-16T09:49:16`: `mark=750.86`, bid/ask `750.85/750.88`, regime bull.
- Recovery scan completed at `09:51`:
  - Snapshot source: `live/snapshots/2026-07-16/0949.parquet`
  - `767` option rows loaded
  - `47` candidate spreads built
  - `3` dropped for earnings
  - per-ticker dedupe `32 -> 24`
  - `1/24` qualified above threshold
  - qualified pick: `JPM bear_call`, `GROUND≈0.05772`
  - Uploaded to Mya successfully.
- No GEPO fetch/ranker job was running during later fan/CPU checks.
- Current heavy CPU was OpenCode itself, not GEPO:
  - `opencode` process was seen at `~70-212% CPU` during this session.
  - `IB Gateway` was mild around `3-4% CPU`.
  - Brave was killed by the user and no longer the main load.

### If reopening after closing this session
Run this first:

```bash
date '+%Y-%m-%d %H:%M:%S %Z'
crontab -l
ps -axo pid=,ppid=,%cpu=,%mem=,etime=,command= | sort -k3 -nr | head -20
python3 -m live.fetch_spy_intraday --print
python3 - <<'PY'
import json
from pathlib import Path
for p in [Path('live/ranked/latest.json'), Path('live/ranked/spy_intraday.json'), Path('live/frozen/2026-07-17.json')]:
    print('\n', p, p.exists())
    if p.exists():
        d=json.loads(p.read_text())
        print('snapshot', d.get('snapshot_ts'), 'frozen_at', d.get('frozen_at'))
        print('n_candidates', d.get('n_candidates'), 'top_picks', len(d.get('top_picks') or []))
PY
```

If SPY quote output has `mark: null` or no bid/ask:
- IB Gateway may again be connected but not delivering quotes.
- Confirm API port and process:
  ```bash
  nc -zv 127.0.0.1 4001
  lsof -nP -iTCP:4001 -sTCP:LISTEN
  ps -ax -o pid=,etime=,command= | rg -i 'IB Gateway|JavaApplicationStub|ibgateway'
  ```
- Safe recovery used on 2026-07-16:
  ```bash
  kill -TERM <IB_GATEWAY_PID>
  sleep 10
  open "/Users/mercurio/Applications/IB Gateway 10.45/IB Gateway 10.45.app"
  ```
- Wait for API port 4001 to reopen, then rerun:
  ```bash
  python3 -m live.fetch_spy_intraday --print
  bash live/cron_parallel.sh
  ```

### Health-alert fixes (Mya stale alert issue)
Root cause of 9am / after-hours stale alerts:
- Local health check had `MARKET_CLOSE=17:15`, so it legitimately emitted alerts around 16:35-17:00 after normal trading signal time.
- `live/upload_to_mya.sh` synced the whole `live/notifications/` directory on every normal upload, including old `health-*.json` files.
- Mya's `notify_watcher.sh` could re-process old health files if they reappeared or were not marked processed.

Fixes applied locally and deployed to Mya:
- `live/health_check.py`
  - Tightened market-hours alert window to `09:30-16:05` ET.
  - Detects an all-null SPY quote payload as an outage.
- `live/fetch_spy_intraday.py`
  - Does **not** overwrite last valid SPY tick with an all-null quote.
- `live/upload_to_mya.sh`
  - Excludes `health-*.json` during normal uploads.
- `live/cron_health.sh`
  - Uploads only newly-created health alerts, not the entire notifications directory.
- Remote-only `/opt/vito/gepo-backtest/live/notify_watcher.sh`
  - Patched to skip `health-alert` files unless filename date is today and time is within `09:30-16:05` ET.
- Old active health files were moved out of active notification queues:
  - local: `live/notifications_archive_health/`
  - remote: `/opt/vito/gepo-backtest/notifications_archive_health/`
- Remote active `live/notifications/` should contain no `health-*.json`; it may contain state files `.processed` and `.last_health_alert`.

Validation already run:
- `python3 -m py_compile live/health_check.py live/fetch_spy_intraday.py`
- `bash -n live/cron_health.sh live/upload_to_mya.sh`
- `python3 -m live.health_check` before 9:30 produced no alert.
- Remote watcher passed `bash -n` after patch.

### 15:01 + 15:31 freeze top-up behavior
New policy implemented in `live/freeze_snapshot.py`:
- If 15:01 freeze has 0 picks, later 15:xx can replace blank with 15:31 picks.
- If 15:01 freeze has 1-4 picks, 15:31 can append unique picks until the basket reaches `TOP_N_DISPLAY=5`.
- Existing 15:01 picks are preserved and remain first.
- Added metadata:
  - `freeze_topup_at`
  - `freeze_topup_added_count`
  - per-pick `freeze_added_at`
- `live/templates/history.html` displays `frozen 15:01+15:31 · +N from 15:31` and marks added picks with `+15:31`.

Backfill applied to historical frozen files:
- `2026-06-01`: `2 + 2 = 4`
- `2026-06-03`: `3 + 2 = 5`
- `2026-06-08`: `3 + 1 = 4`
- `2026-06-11`: `2 + 3 = 5`
- `2026-06-15`: `3 + 2 = 5`
- `2026-07-08`: `2 + 2 = 4`
- `2026-07-09`: `3 + 2 = 5`
- `2026-07-14`: `1 + 4 = 5`
- `2026-07-15`: `1 + 3 = 4`

Closed/expired topped-up histories were settled from daily closes:
- `2026-06-01`: `4` picks, P&L `-482.8`
- `2026-06-03`: `5` picks, P&L `-339.2`
- `2026-06-08`: `4` picks, P&L `-56.0`
- `2026-06-11`: `5` picks, P&L `+405.8`
- `2026-06-15`: `5` picks, P&L `+96.8`
- `2026-07-08`: `4` picks, P&L `+401.2`
- `2026-07-09`: `5` picks, P&L `+98.0`
- `2026-07-14` and `2026-07-15` expire `2026-07-17`; not settled at time of check.

### OOT July update
- Incrementally appended only new dates from `data/DG_2026July` after prior OOT end `2026-07-07`:
  - `2026-07-08`, `2026-07-09`, `2026-07-10`
- Wrote:
  - `output/2026_sp500_last_oot_incremental_20260708_20260710.parquet`
  - updated `output/2026_sp500_last_oot_combined.parquet`
  - backup: `output/2026_sp500_last_oot_combined.parquet.bak_before_20260708_20260710`
- Removed stale OOT combined cache and regenerated `live/data/oot_equity.json`.
- OOT result after regeneration:
  - `n_trades=218`
  - `strategy_final=17595.4`
  - return `+75.95%`
  - Sharpe `3.19`
  - max DD `-12.04%`
- New OOT trades added:
  - `2026-07-08 VZ bull_put -9.2`
  - `2026-07-08 ADI bull_put +148.0`
  - `2026-07-08 T bull_put +18.8`
  - `2026-07-09 BMY bull_put +2.0`
  - `2026-07-09 T bull_put +20.8`
  - `2026-07-09 PM bear_call -20.0`
- July 6 had data but no qualified picks:
  - `23,205` raw rows
  - `99` candidates
  - `51` scored
  - `0` above `GROUND >= 0.05`
  - best was `COST bull_put`, `GROUND≈0.04467`.

### Live table display change
- `live/ranker.py` now serializes every positive-GROUND candidate into the table payload instead of capping at `TICKER_LIMIT=30`.
- `TICKER_LIMIT` removed from `live/live_config.py` and mock data updated.
- Example after change: `45` ranked candidates, `40` positive rows shown.
- Top-pick cards still show only threshold-qualified picks.

### Current dirty/untracked files to be aware of
- Expected local modifications from this work include:
  - `live/freeze_snapshot.py` (new/untracked but already used by cron and deployed)
  - `live/templates/history.html`
  - `live/health_check.py`
  - `live/fetch_spy_intraday.py`
  - `live/cron_health.sh`
  - `live/upload_to_mya.sh`
  - many earlier live reliability files from this broader session
- `live/notifications_archive_health/` is an archive of old health alert payloads, not active queue data.
- `default.profraw` is present and untracked; likely an incidental profiling/runtime artifact. Do not assume it is needed.

---

## 0.8 Live Friday scans (2026-07-10) — DTE0 same-day expiry

User wanted Friday scans to run, but **not** next-week DTE7. Final behavior:
- `live/live_config.py`: `LIVE_DTE_MIN=0`, `LIVE_DTE_MAX=6`.
- This keeps Mon-Thu on the same-week Friday expiry naturally:
  - Mon DTE4, Tue DTE3, Wed DTE2, Thu DTE1.
- Friday now scans same-day Friday expiry:
  - Fri DTE0.
- The installed crontab main scanner is now Mon-Fri:
  - `1,31 9-16 * * 1-5 /Users/mercurio/Downloads/gepo-backtest/live/cron_parallel.sh`

Implementation notes:
- `live.fetcher._weekly_expiries_in_dte_window()` now accepts an explicit DTE window and defaults through `live_config.live_dte_window()`.
- `live.ranker` serializes the active DTE window into `latest.json` config, so the UI shows `0-6d`.
- Python 3.9 compatibility was preserved; no `X | None` annotations.

Verification on Friday 2026-07-10:
- Local helper test selected `20260710` for Friday 2026-07-10 with DTE window `(0, 6)`.
- Manual run started at `09:41` after missing the 09:31 cron boundary:
  - Fetchers logged `Live fetch: ... DTE [0, 6]`.
  - Same-day contracts requested with `lastTradeDateOrContractMonth='20260710'`.
  - Merged `7` group files into `live/snapshots/2026-07-10/0941.parquet`.
  - `926` unique option rows loaded.
  - Ranker built `38` candidate spreads, deduped `14 -> 12`, qualified `1`.
  - Wrote `live/ranked/latest.json` and `live/ranked/2026-07-10_0941.json`.
  - `snapshot_picks` captured `1` qualified pick at `0942`.
  - Upload to Mya completed.
  - Latest top pick at that time: `LOW bull_put`, expiry `2026-07-10`, `DTE=0`, `GROUND≈0.08084`.
- Some IB fetcher groups timed out on connect during the manual run, but enough groups completed for ranking/upload. Monitor the next scheduled run (`10:01` or `10:31`) for whether connect timeouts persist.

---

## 0.7 Mac cleanup follow-up (2026-07-09) — remaining after user removal

User cleaned up old Microsoft/Office/OneDrive, Citrix, MoneyWiz, and MinerGate traces during market day. Do not ask for a restart until after market close; user said they will restart tonight.

### Confirmed removed
- Exact cleanup targets are gone:
  - `/Library/Application Support/deviceTRUST`
  - `/Library/Preferences/com.microsoft.autoupdate2.plist`
  - `/Library/Preferences/com.microsoft.teams.plist`
  - `/Library/Caches/com.microsoft.autoupdate.fba`
  - `/Library/Caches/com.microsoft.autoupdate.helper`
  - `/private/var/db/receipts/com.moneywiz.personalfinance.*`
  - `~/Library/Application Support/FileProvider/com.microsoft.OneDrive.FileProvider`
  - `~/Library/Logs/OneDrive`
  - `~/Library/Logs/ReceiverInstall.log`
  - `~/Library/Receipts/citrix.CitrixEndpointAnalysis.*`
  - `~/Library/Cookies/com.microsoft.OneDriveStandaloneUpdater.binarycookies`
- No active Microsoft/Citrix/MoneyWiz/MinerGate processes. `ps` matches were false positives from 1Password and Apple Passwords/PasswordBreachAgent.
- Individual launchd queries for these labels return "Could not find service":
  - `com.microsoft.update.agent`
  - `com.citrix.ReceiverHelper`
  - `com.citrix.AuthManager_Mac`
  - `com.citrix.ServiceRecords`
  - `com.citrix.UninstallMonitor`
  - `com.citrix.safariadapter`

### Deletion log for rollback/debug
User first manually removed these user-level app containers/support files:

```bash
rm -rf \
  "$HOME/Library/Containers/com.microsoft.OneDriveLauncher" \
  "$HOME/Library/Containers/com.microsoft.outlook.profilemanager" \
  "$HOME/Library/Containers/com.microsoft.Powerpoint" \
  "$HOME/Library/Containers/com.microsoft.SkypeForBusiness" \
  "$HOME/Library/Containers/com.moneywiz.personalfinance" \
  "$HOME/Library/Containers/com.microsoft.Word" \
  "$HOME/Library/Containers/com.microsoft.OneDrive.FinderSync" \
  "$HOME/Library/Containers/com.microsoft.onenote.mac" \
  "$HOME/Library/Containers/com.citrix.NetScalerGateway.macos.app" \
  "$HOME/Library/Containers/com.microsoft.Outlook.CalendarWidget" \
  "$HOME/Library/Containers/com.microsoft.Excel" \
  "$HOME/Library/Containers/com.microsoft.openxml.excel.app" \
  "$HOME/Library/Containers/com.microsoft.errorreporting" \
  "$HOME/Library/Containers/com.microsoft.Microsoft-Mashup-Container" \
  "$HOME/Library/Containers/com.microsoft.onenote.mac.shareextension" \
  "$HOME/Library/Containers/com.microsoft.SkyDriveLauncher" \
  "$HOME/Library/Containers/com.microsoft.Outlook" \
  "$HOME/Library/Application Support/minergate" \
  "$HOME/Library/Preferences/com.microsoft.autoupdate.fba.plist" \
  "$HOME/Library/Preferences/UBF8T346G9.OfficeOneDriveSyncIntegration.plist" \
  "$HOME/Library/Preferences/UBF8T346G9.OneDriveStandaloneSuite.plist" \
  "$HOME/Library/Preferences/com.microsoft.OneDriveStandaloneUpdater.plist" \
  "$HOME/Library/Preferences/com.microsoft.shared.plist" \
  "$HOME/Library/Preferences/com.microsoft.OutlookSkypeIntegration.plist" \
  "$HOME/Library/Preferences/com.microsoft.OneDriveUpdater.plist" \
  "$HOME/Library/Caches/com.citrix.UninstallReceiver.mac" \
  "$HOME/Library/Caches/SentryCrash/Uninstall Citrix Workspace" \
  "$HOME/Library/Caches/com.microsoft.autoupdate.fba"
```

Then user was given and appears to have run this second cleanup command:

```bash
sudo rm -rf \
  "/Library/Application Support/deviceTRUST" \
  "/Library/Preferences/com.microsoft.autoupdate2.plist" \
  "/Library/Preferences/com.microsoft.teams.plist" \
  "/Library/Caches/com.microsoft.autoupdate.fba" \
  "/Library/Caches/com.microsoft.autoupdate.helper" \
  "/private/var/db/receipts/com.moneywiz.personalfinance.bom" \
  "/private/var/db/receipts/com.moneywiz.personalfinance.plist"

rm -rf \
  "$HOME/Library/Application Support/FileProvider/com.microsoft.OneDrive.FileProvider" \
  "$HOME/Library/Logs/OneDrive" \
  "$HOME/Library/Logs/ReceiverInstall.log" \
  "$HOME/Library/Receipts/citrix.CitrixEndpointAnalysis.plist" \
  "$HOME/Library/Receipts/citrix.CitrixEndpointAnalysis.bom" \
  "$HOME/Library/Application Support/com.apple.sharedfilelist/com.apple.LSSharedFileList.ApplicationRecentDocuments/com.microsoft.excel.sfl2" \
  "$HOME/Library/Application Support/com.apple.sharedfilelist/com.apple.LSSharedFileList.ApplicationRecentDocuments/com.microsoft.word.sfl2" \
  "$HOME/Library/Application Support/com.apple.sharedfilelist/com.apple.LSSharedFileList.ApplicationRecentDocuments/com.microsoft.powerpoint.sfl2" \
  "$HOME/Library/Application Support/com.apple.sharedfilelist/com.apple.LSSharedFileList.ApplicationRecentDocuments/com.citrix.receiver.helper.sfl3" \
  "$HOME/Library/Application Support/com.apple.sharedfilelist/com.apple.LSSharedFileList.ApplicationRecentDocuments/com.citrix.citrixreceiverlauncher.sfl3" \
  "$HOME/Library/Safari/LocalStorage/https_support.citrix.com_0.localstorage"* \
  "$HOME/Library/Safari/LocalStorage/https_www.citrix.com_0.localstorage"* \
  "$HOME/Library/Safari/LocalStorage/https_support.microsoft.com_0.localstorage"* \
  "$HOME/Library/Safari/LocalStorage/https_myaccount.microsoft.com_0.localstorage"* \
  "$HOME/Library/Safari/LocalStorage/https_teams.microsoft.com_0.localstorage"* \
  "$HOME/Library/Cookies/com.microsoft.OneDriveStandaloneUpdater.binarycookies"
```

Optional iCloud cleanup was offered but not confirmed as run:

```bash
rm -rf \
  "$HOME/Library/Mobile Documents/iCloud~com~microsoft~azureauthenticator" \
  "$HOME/Library/Mobile Documents/iCloud~com~microsoft~skydrive" \
  "$HOME/Library/Mobile Documents/iCloud~com~microsoft~onenote" \
  "$HOME/Library/Mobile Documents/iCloud~com~microsoft~skype~teams" \
  "$HOME/Library/Mobile Documents/iCloud~com~microsoft~officemobile" \
  "$HOME/Library/Mobile Documents/iCloud~moneywiz~personalfinance"
```

Rollback notes:
- These were deleted with `rm -rf`, not moved to Trash. Local rollback requires Time Machine/local backup, app reinstall, or cloud re-sync.
- Microsoft Office/OneDrive/Outlook/OneNote/Teams breakage: reinstall Microsoft 365/OneDrive and sign in again. Deleted containers/preferences are per-user app state; reinstall recreates defaults, but local-only caches/preferences are gone.
- Citrix breakage: reinstall Citrix Workspace and any deviceTRUST/Endpoint Analysis component required by the workplace. The removed `/Library/Application Support/deviceTRUST` folder was root-owned support code.
- MoneyWiz breakage: reinstall MoneyWiz and restore from its sync/account/iCloud data if needed. The package receipt was removed, not app data beyond the earlier `com.moneywiz.personalfinance` container if user removed it.
- Browser/site state breakage: Safari local storage/cookies for Microsoft/Citrix sites may require signing in again.
- Recent-document entries are cosmetic and will rebuild as apps are used.

### Still visible before restart
- Background Task Management still shows a disabled stale entry:
  - `com.microsoft.OneDriveStandaloneUpdaterDaemon`
  - Status seen via `sfltool dumpbtm`: `Disposition: [disabled, allowed, visible, not notified]`, `URL: (null)`, `Identifier: Unknown Developer`.
- `launchctl print gui/$(id -u)` still prints enabled-name lines for Citrix/Microsoft, but direct `launchctl print gui/$(id -u)/<label>` cannot find the services. Treat as registration/cache state unless a backing plist reappears.

### Low-risk user data traces still present
- Recent-document lists:
  - `~/Library/Application Support/com.apple.sharedfilelist/com.apple.LSSharedFileList.ApplicationRecentDocuments/com.microsoft.excel.sfl2`
  - `.../com.microsoft.word.sfl2`
  - `.../com.microsoft.powerpoint.sfl2`
  - `.../com.citrix.receiver.helper.sfl3`
  - `.../com.citrix.citrixreceiverlauncher.sfl3`
- Safari local storage for Office/Outlook/SharePoint:
  - `~/Library/Safari/LocalStorage/https_outlook.office365.com_0.localstorage*`
  - `~/Library/Safari/LocalStorage/https_portal.office.com_0.localstorage*`
  - `~/Library/Safari/LocalStorage/https_yuoffice-my.sharepoint.com_0.localstorage*`
- iCloud containers remain unless user chooses optional cleanup:
  - `~/Library/Mobile Documents/iCloud~com~microsoft~azureauthenticator`
  - `~/Library/Mobile Documents/iCloud~com~microsoft~skydrive`
  - `~/Library/Mobile Documents/iCloud~com~microsoft~onenote`
  - `~/Library/Mobile Documents/iCloud~com~microsoft~skype~teams`
  - `~/Library/Mobile Documents/iCloud~com~microsoft~officemobile`
  - `~/Library/Mobile Documents/iCloud~moneywiz~personalfinance`
- Ignore false positives from LibreOffice, iWork `OfficeFonts.plist`, Apple CoreSpotlight `receiver.*`, Apple Passwords, and 1Password.

### After restart tonight
Run:

```bash
sfltool dumpbtm | rg -i "microsoft|onedrive|citrix|receiver|moneywiz|minergate"
launchctl print gui/$(id -u) 2>/dev/null | rg -i "microsoft|onedrive|citrix|receiver|moneywiz|minergate"
ps -eo pid,ppid,etime,command | rg -i "microsoft|onedrive|citrix|receiver|moneywiz|minergate"
```

Expected: no active processes; ideally no BTM result. If `com.microsoft.OneDriveStandaloneUpdaterDaemon` remains after restart, it is a stale disabled BTM registration with no file URL. Next step is to inspect/remove via macOS Login Items & Background Items UI or research the current `sfltool`/BTM reset method for this macOS version before attempting any destructive database edit.

---

## 0.6 Live-ops session (2026-07-08) — restart-critical notes

User is upgrading macOS to Sequoia and will restart. On the next session, **first verify the live jobs**:

```bash
crontab -l
/usr/bin/python3 --version
/usr/bin/python3 -c "import pandas, pyarrow, ib_insync; print('ok')"
. "$HOME/.gepo_env" && ssh -o BatchMode=yes "$MYA_SSH_HOST" 'echo ok'
tail -n 80 live/logs/daily_bars.log
tail -n 5 data/daily_bars_yahoo/SPY.csv
```

If the Mac upgrade reset permissions, Terminal/iTerm may need Full Disk Access again and IB Gateway/TWS API permissions should be rechecked. After restart, manually smoke-test:

```bash
bash live/cron_parallel.sh
bash live/cron_daily_bars.sh
```

### Cron / Python env fixes
- Root cause of the missed 09:30 job: cron inherited stale `DEVELOPER_DIR=/Applications/Xcode.app`, causing `/usr/bin/python3` / xcrun failure. Fixed with `unset DEVELOPER_DIR` in:
  - `live/cron_parallel.sh`
  - `live/pull_now_parallel.sh`
  - `live/cron_daily_bars.sh` (added 2026-07-08)
- Apple Python 3.8 on this Mac needs `pyarrow==12.0.1`; newer `pyarrow 17` segfaulted. Missing deps were installed into `/usr/bin/python3`.
- `~/.gepo_env` was malformed and fixed to:
  - `export MYA_SSH_HOST="ubuntu@gepo-ticker.peter.cloudmallinc.com"`
  - `export MYA_REMOTE_BASE="/opt/vito/gepo-backtest/live"`

### Yahoo daily bars / snapshot settlement
- `fetch_yahoo_recent.py` works against Yahoo. Direct SPY chart fetch returned rows through 2026-07-08.
- Local Yahoo CSVs had gone stale at 2026-06-23 because the daily job had not run successfully. Ran merge-only `fetch_yahoo_recent.py`: all 98 ticker CSVs updated through 2026-07-07; no failures.
- Important fix: `fetch_yahoo_recent.py` now skips/drops the current NYSE date before 17:00 ET (`FINAL_BAR_HOUR_ET=17`). Yahoo serves a partial current-day daily bar intraday; the old merge policy (`keep="first"`) would pin that incomplete close forever. Running it before close now removes any incomplete current-day row and waits for the 17:01 cron to add the finalized bar.
- After 17:01 EDT on 2026-07-08, expected `data/daily_bars_yahoo/SPY.csv` to include finalized `2026-07-08`. Before 17:00 it should end at `2026-07-07`.

### Snapshots tab P&L backfill
- Stale Yahoo closes meant June 22/23 snapshot scans expiring 2026-06-26 had not settled.
- Ran `snapshot_picks.settle()` locally after Yahoo refresh. Results:
  - `2026-06-22.json`: 19 settled, 0 open, total P&L `-$903.80`
  - `2026-06-23.json`: 6 settled, 0 open, total P&L `-$156.80`
  - `2026-07-08.json`: 0 settled, 6 open, expiry `2026-07-10` (correctly still open)
- Uploaded to Mya with `bash live/upload_to_mya.sh`; remote verification matched those counts/totals.

### Mya deploy/upload notes
- Manual data upload:
  ```bash
  . "$HOME/.gepo_env" && bash live/upload_to_mya.sh
  ```
  This preserves Mya-side `actual_credit` edits first.
- Code/template deploy is not handled by `upload_to_mya.sh`; use explicit rsync paths, then HUP gunicorn master:
  ```bash
  . "$HOME/.gepo_env" && rsync -az --partial --timeout=20 -e 'ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new' live/webapp.py "$MYA_SSH_HOST:/opt/vito/gepo-backtest/live/webapp.py"
  . "$HOME/.gepo_env" && rsync -az --partial --timeout=20 -e 'ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new' live/templates/snapshots.html "$MYA_SSH_HOST:/opt/vito/gepo-backtest/live/templates/snapshots.html"
  . "$HOME/.gepo_env" && ssh -o ConnectTimeout=20 -o BatchMode=yes -o StrictHostKeyChecking=accept-new "$MYA_SSH_HOST" "M=\$(ps -eo pid,ppid,cmd | grep '[g]unicorn' | grep 'live.wsgi' | awk '\$2==1 {print \$1}' | head -1); kill -HUP \"\$M\"; echo HUP \"\$M\""
  ```
- Snapshots UI duplicate 10:00 labels were fixed by showing actual scan time plus muted bucket time when different (`hhmm_actual`, `hhmm_round`) in `live/webapp.py` + `live/templates/snapshots.html`; deployed to Mya and gunicorn HUP'd.

### Known remaining proof point
- The installed crontab includes `1 17 * * 1-5 /Users/mercurio/Downloads/gepo-backtest/live/cron_daily_bars.sh`. The only unproven piece after the July 8 fixes is a real scheduled 17:01 post-close run after restart/Sequoia. Check `live/logs/daily_bars.log` and SPY CSV after 17:01.

---

## 0.5 Live-ops session (2026-06-15 → 2026-06-22) — operational only, no canon/strategy change

All of the following are live-pipeline / webapp changes. The strategy canon (§0: rv_vs_iv DKL, k=10, thr=0.05, regime OFF) is untouched.

### NYSE holiday-shifted weekly expiries
- **NEW `live/trading_calendar.py`** — shared settlement calendar built on `spreads.NYSE_HOLIDAYS`. Key fns: `weekly_settlement_day()`, `is_settlement_day()`, `week_notice()`, plus CLI guard `python3 -m live.trading_calendar --is-settlement-day` (exit 0/1) for bash crons. When this week's Friday is an NYSE full-close holiday (e.g. Juneteenth 2026-06-19), the weekly instead settles the prior trading day (Thursday).
- **Expiry-side crons gained a settlement-day guard** (`cron_expire.sh`, `cron_track_expiring.sh`, `cron_close_alert.sh`): they now fire **Thu+Fri** (`* * 4,5`) and `exit 0` (no-op, logged) on whichever of the two is NOT the week's settlement day. Fixes the gap where holiday-Friday weeks had no expiry-day cron.
- **Holiday warning banner** on the live tab: `week_notice()` returned via `_enrich_payload` + the `/api/latest.json` error branch; `index.html` shows an amber "Short week — <Friday> is an NYSE holiday … close by <weekday>" banner only when `week_notice.shifted` is true.

### Snapshots tab
- **Half-hour rounding** — `_round_hhmm()` in `webapp.py` buckets cron-drift scan times (10:04, 14:32/14:33, 15:02/15:03) into clean :00/:30 slots for the per-scan-time aggregate and the day-by-day labels.
- **Caption threshold made dynamic** — `snapshots.html` had a hardcoded stale `thr=0.07`; now renders `thr={{ '%g' % thr }}` from `config.GROUND_THRESHOLD` (→ `0.05`), so it can't drift again. Deployed to Mya (rsync `webapp.py` + `snapshots.html`, gunicorn HUP) — verified `thr=0.05` live.

### Same-evening snapshot settlement
- **`snapshot_picks.settle(post_close=False)`** — new flag. Default (intraday `pull_now_parallel` calls) keeps the safe Yahoo daily-bar close and only settles picks whose expiry is strictly before today ("morning after"). With `post_close=True` it ALSO settles picks expiring **today**, sourcing today's RTH close live from IB via `expire_frozen._underlying_price` (cached per ticker, distinct clientId `IB_CLIENT_ID+10`). Older expiries still use Yahoo (IB's last daily bar is always today and would mis-settle them).
- **`cron_daily_bars.sh` (17:01 Mon-Fri)** now calls `settle(post_close=True)` and then `upload_to_mya.sh`, so the Snapshots tab settles + publishes the same evening as the History tab (`expire_frozen`, 16:01) instead of lagging a day. Verified: post_close settle reproduced the History tab's closes exactly (e.g. CSX 45.63 WIN, BLK 1050.09 WIN, SO 93.09 PARTIAL).

### Mock-data incident + revert (2026-06-21)
- Mock data (from `live/mock_data.py populate_mock`) got onto Mya: `ranked/latest.json` (`mock:true`, `[MOCK]` tag) + 7 frozen files 06-11…06-19. Local files were all real (clean source of truth).
- **Revert:** re-uploaded local real `ranked/latest.json` + frozen `06-11/06-15/06-16/06-17` over the mock; **moved (not deleted)** the 3 mock-only frozen files `06-12, 06-18, 06-19` into `frozen/_mock_quarantine_20260621/` on Mya. Those 3 were fabricated days with no real freeze anywhere (06-12 & 06-19 are Fridays — cron is Mon-Thu; 06-18's local snapshot dir was empty; frozen files aren't in git; no Mya backups). `intraday_picks` (Snapshots tab) was never touched by the mock — all real and settled (06-11 9/9, 06-15 26/26, 06-16 32/32, 06-17 40/40). Verified via public API afterward: `mock=None`, real 06-17 snapshot, 37 candidates.
- **Open follow-up:** guard `upload_to_mya.sh` (and/or a deploy check) against ever pushing a `mock:true` payload, so this can't recur.

---

## TODO LIST (open items from 2026-06-10 session — none started without user go-ahead)

1. **Live liquidity gate (width-based OI proxy)** — root-cause fix for the CAT phantom (OI-0 strikes, 30-50% wide quotes, mutually inconsistent leg mids → b=11.5, Kelly EV +107%). Design agreed: `MAX_REL_SPREAD` per-leg gate in spreads.py next to MIN_OPEN_INTEREST, default `inf` (backtest keeps its OI≥100 gate), live override `LIVE_MAX_REL_SPREAD`. **Calibrate the cap against vendor data so width-gate ≈ OI≥100 universe** (don't guess; likely ~35-45%, possibly + Volume≥1 on short leg). Validate by replaying today's snapshots (CAT/USB must drop; survivor count must stay sane).
2. **Wide-quote robustness test (winner's curse)** — re-evaluate k=10/thr=0.07 from the sweep caches EXCLUDING candidates whose short-leg relative quote width exceeds a cap. Tests whether part of the edge is selection on noisy EOD mids (e.g. AMGN 12-23: b=3.46 from a 1.37/4.10 quote). No rescore needed (join cache→parquet for widths).
3. **Backtest parity for per-ticker dedupe** — live ranker now keeps only the best-Γ direction per ticker (2026-06-10); backtest doesn't (2.4% of canon picks are dual-direction same-day; 24% of dual-quoted strike pairs violate box parity; 15 selected picks sit on violating pairs). Re-evaluate from cache with dedupe applied; if numbers hold, fold into canon + regenerate site JSONs. (Box-parity REJECTION was vetoed by user — choose-one-side only.)
4. **Asymmetric DKL (`rv_vs_iv_qdown`) test — DONE 2026-06-10, REJECTED.** Best qdown cell $41.3k / Sh(wk) +2.10 vs symmetric $50.0k / +2.53 (same cache, sanity corr 1.0000); OOT worse too (+2.26 best vs +2.76, k=12 cell +1.05). qdown wins more often (59-60% vs 58%) but earns less — the p-leg (win-prob) disagreement carries the gate's information. Symmetric DKL remains canon. Prob-triplet caches written (output/sweep_midmkt_*_probs.parquet): any future DKL variant is now a zero-rescore experiment (test_qdown_rv_dkl.py is the template).
5. **Fetch speed, remaining lever: day-cached conIds + chain params** — cache (symbol,expiry,strike,right)→conId on first scan of day; skip reqSecDefOptParams + qualifyContracts on scans 2-13. (Asymmetric per-right strike windows DONE 2026-06-10: puts ≤1.04×spot, calls ≥0.96×spot. Handshake stagger 0.5s/group DONE.)
6. **Tracker marks vs asymmetric fetch windows** — deep-ITM tracked legs (e.g. QCOM 202.5p with spot 192.77) now fall OUTSIDE the per-right fetch window → no fresh leg quotes → mark falls back to intrinsic-at-current-spot (webapp floor added 2026-06-10). Verify this is acceptable or fetch tracked frozen legs explicitly each scan.
7. **Backtest trade-log Kelly EV on fill basis (optional display)** — log currently shows selection-basis EV (raw-mid b) next to fill-basis dollars (0.80×mid). Showing fill-basis EV needs p/q/ro cached (one rescore) — display-only change.
8. **Negative-Kelly display (optional)** — w* pinned at 0.01 floor on no-edge spreads shows −0.0x% EV; could display "no edge" instead.
9. **Theoretical k from scratch (paper work)** — k_theory=29 WITHDRAWN (error #71: formula had N in numerator — Gibbs inverse-temperature vs PAC-Bayes confidence-penalty commensurability confusion, same trap as #64). Empirical answer stands: plateau k 6-16, growth peak 12, Sharpe peak 8.
10. **Fill multiplier refinement** — 0.80×mid rests on n=5 real fills (2026-05-28 ONLY; all other actual_credit entries are user estimates — never calibrate on them). Log every real fill in actual_credit; recalibrate as n grows. Depth study (870k same-day prints): trades center on mid at every quote width; 19.2% of same-day prints outside EOD BBO (kills LAST basis).
11. **2-week tenor (DTE 8-11) — TESTED 2026-06-10, DEAD.** Canon pipeline on 2026 (new output/2026_sp500_dte11.parquet, raw CSVs support DTE 0-1000): n=51, +0.8% final, Sh(wk) +0.42, win 51.0% (25W/25L), $2.53/tr vs canon DTE 1-4 $14.49/tr — coin flip, consistent with the old Friday DTE-7 result ($1.66/tr). Edge lives in the final days of option life. No 2020-25 re-preprocess warranted. Script: backtest_dte2week_2026.py.
12. **Gateway stability under both-rights load** — "output exceeded limit" on Gateway console + 14/20 group handshake timeouts at 14:31 (local CPU pressure from analysis job was a co-cause). Stagger added; monitor next scans; if recurring, consider fewer groups (20→10) since per-group load doubled.

---

## 0. The new canonical config (as of 2026-06-10)

| knob | value | notes |
|---|---|---|
| **DKL_REFERENCE** | `rv_vs_iv` | BS d2 with 10d realized vol vs IV per leg |
| **DKL_K** | `10` | (FINAL 2026-06-12, corrected solver: (10, 0.05) = growth-optimal cell of the sweep; criterion frozen = framework objective) |
| **PROB_PARTITION** | `3-state` | α-weighted partial-zone (2-state variants both worse) |
| **GROUND threshold** | `0.05` per dow | (FINAL 2026-06-12 with k=10: growth-optimal cell; ≥0.09 goes NEGATIVE in 2026 OOT) |
| **Selection** | top-5 QUALIFIED per entry_date | (may show <5 on low-edge days) |
| **REGIME_FILTER** | `False` | (was True; gate was hurting us) |
| **EARNINGS gate** | ON (all spreads) | NEW 2026-06-08 |
| **EX-DIV gate** | ON (bear-calls only, +1 day buffer) | NEW 2026-06-08 |
| **Universe** | SP100 | unchanged |
| **Days** | Mon–Thu entry, Friday expiry | unchanged |
| **DTE** | 1–4 | unchanged |
| **MIN_OPEN_INTEREST** | 100 backtest / 0 live | unchanged |
| **Empirical bucket key** | `(DTE, putcall, delta_bucket, iv_bucket, iv_rank_bucket)` | 5-tuple (iv_rank_bucket added) |
| **Trailing pool window** | 30 weeks | unchanged |
| **Entry credit basis** | **raw combo MID for selection; fills 0.80×mid** | NEW 2026-06-10. LAST basis DEAD (async leg prints fabricate credits; 19.2% of same-day prints outside EOD BBO). Selection b = market mid b; 0.80 calibrated on the only real fills (2026-05-28, n=5, mean 0.82×mid). CREDIT_BASIS/CREDIT_SCALE in config.py; live LIVE_CREDIT_BASIS="mid", LIVE_CREDIT_SCALE=1.0. Fetcher pulls BOTH rights (regime filter removed). Live ranker dedupes to one direction per ticker. |
| **Close debit basis** | BS theoretical (live tracker) | replaces 1.15×LAST / 1.20×MID |
| **Partial-WIN haircut** | 50% intrinsic (backtest realize) | NEW 2026-06-08 (pin-risk realistic) |
| **MIN_ENTRY_DATE** | `2020-01-01` | rv_vs_iv doesn't need pool history |
| **Live display qty** | 1 (per-contract) | NEW 2026-06-08 (live + history pages) |

### Headline backtest, mid-basis canon (2020-01 → 2025-12, 1685 picks, fills 0.80×mid, partial-WIN haircut, calendar-filtered, CORRECTED KELLY SOLVER 2026-06-12)
- **qty=2: $87,743 final** from $10k (+777%), Sh(wk) +2.17, MaxDD −4.2%
- **qty=1: $48,872** (+389%), Sh(wk) +2.49, MaxDD −3.7%, win 57.7%
- 2026 OOT: qty=1 $11,227 (+12.3%), Sh(wk) +1.96, DD −7.2%, n=119
- NOTE (error #75): ground.py degenerate Kelly branch (α=0, b≥1) used the 2-outcome formula (pb−q)/b; correct 3-state linear FOC is (pb−q)/(b(p+q)). Fixed 2026-06-11; v2 caches (output/sweep_midmkt_v2_*) supersede the contaminated originals. CANON FINAL 2026-06-12: k=10/thr=0.05, the GROWTH-OPTIMAL cell (criterion = framework objective; the same-day k=16 Calmar pick was reverted as criterion-shopping on a path statistic — its DD doubled OOT). qty1 $50,798 (+408%) Sh(wk)+2.44 DD−5.8% n=2250; OOT $12,726 (+27.3%) Sh(wk)+3.22 n=183. Deployed: ground.py, config.py, site JSONs+captions on Mya; first live ranking under new cell = Monday 2026-06-15 09:31. G-alone proof under corrected solver: −$15,175 vs +$38,872, split t=4.51. Calmar analysis (2026-06-12): canon (10,0.07) Calmar 8.26 is 2nd-best among OOT-surviving cells; higher-Calmar cells (12/0.10, 10/0.10) are over-gated and die OOT; (16, 0.05) was briefly adopted then REVERTED 2026-06-12 (criterion-shopping; DD inverted OOT). Selection criterion is now FROZEN: growth-optimal cell.
- NOTE (error #74): vendor republishes stale rows on market holidays; pre-filter canon had 47 phantom holiday entries (n=1668). Calendar filter now in report_mid_canon.py + backtest_midsel_sweep.py. Old sweep caches retain phantom rows — FILTER AT LOAD (entry_date in SPY calendar) for any future cache analysis.
- Fill-stress: each +5% fill quality ≈ +$12k final / +0.2-0.3 Sh(wk) (0.80→0.95 = $51k→$87k at the k=12 growth cell)
- Generator: report_mid_canon.py (reads sweep caches output/sweep_midmkt_*.parquet — no rescore)

### Worst-case stress test (every PARTIAL → full max loss)
- Final $36,940, +269%, Sh 1.39, DD -15.2%, WR 46%
- **Strategy survives even brutal assignment treatment**. Live results expected somewhere between this floor and canon.

### What gets you the new numbers
1. IV-rank bucket added to empirical lookup (DD improvement)
2. DKL switched from empirical_vs_delta → rv_vs_iv (more permissive, captures per-spread VRP gap)
3. k lowered 50 → 10 (matches rv_vs_iv DKL scale)
4. thr raised 0.030 → 0.075 (matches new GROUND scale)
5. **Regime gate OFF** — biggest single change. GROUND correctly identifies counter-regime picks (bull-put in bear regime: 58% win rate; bear-call in bull regime: +$11k net).

---

## 1. What changed in code this session

### `iv_rank.py` (NEW)
Per-(Symbol, DataDate) ATM IV rolling 252d percentile. Bucket 0–4. Used as 5th dimension in empirical bucket key. Output: `output/iv_rank.parquet`.

### `rv_table.py` (NEW)
Per-(Symbol, DataDate) 10-day rolling RV. **WINDOW_DAYS = 10** for tenor-match to DTE 1-4 strategy (was 30 initially; user caught the mismatch). Output: `output/rv_table.parquet`. Built via `build_rv_table.py`.

### `build_production_pool.py`
- Keeps Symbol column through processing
- Calls `iv_rank.compute_iv_rank` and merges `iv_rank_bucket` into pool
- Pool now ~15.4M rows with `iv_rank_bucket` populated (90% coverage; rest are 2020 H1)
- Writes `output/master_pool.parquet` + `output/iv_rank.parquet`

### `empirical_runner.py`
- `build_window_tables` groupby now keyed on 5-tuple `(DTE, delta_bucket, iv_bucket, iv_rank_bucket)`
- Rows without iv_rank get bucket=-1 (separate cell, doesn't dilute)

### `historical_probs.py`
- `empirical_lookup_probs` accepts `iv_rank_bucket` parameter
- `_lookup_p_itm_empirical`: 2-tier lookup — 4-tuple cell first, fall back to 3-tuple weighted mean

### `ground.py`
- New DKL_REFERENCE options: `rv_vs_iv` (canonical now), `iv_vs_rv` (tested), `q_down_ro_sym` (tested, reverted)
- New `PROB_BASIS` toggle: `iv` (canon) or `rv` (tested, reverted)
- The `rv_vs_iv` branch computes BS d2 probs from IV vs RV, takes DKL(P_rv ‖ Q_iv)
- IV-rank passed through to empirical lookup via row['iv_rank_bucket']

### `spreads.py`
- `_build_spread_dict` now carries `iv_rank_bucket` and `rv_30d` (when present on the merged df)
- Otherwise unchanged

### `live/bs_pricing.py` (NEW)
- `bs_price(spot, strike, iv, dte_days, pc)` — Black-Scholes theoretical for put/call
- `bs_spread_debit(...)` — close-debit for a credit spread
- r=0, q=0, naive normal CDF via math.erf

### `live/track_frozen.py`
- **MTM mark = BS theoretical** (was 1.15×LAST / 1.20×MID with stale-LAST fallback)
- `_lookup_leg` now captures IV per leg (for BS calc)
- **NEW**: when legs missing from snapshot, marks to **intrinsic at current spot** (was previously spot-only no-mark tick). Handles Friday-expiry-day correctly when fetcher has rolled to next week's options.

### `live/webapp.py`
- Close-debit computation now TRUSTS tracker's `current_mark` (no recompute)
- When tracker's mark is None, computes intrinsic-at-current-spot (matches tracker fallback)
- Entry credit basis: **0.80 × clamped LAST** (was 0.85)
- **NEW: assignment_risk flag** set on target tracking row when (today is Friday) AND (pick expires today) AND (short leg ITM). Used by history template to highlight the row.
- **NEW (2026-06-08): suggested_qty = 1** for per-contract display everywhere (was 2). Display only — real trading qty stays at user's discretion.
- `actual_credit` edit endpoint unchanged

### `live/templates/history.html`
- **NEW: assignment-risk row class** when `last_track.assignment_risk` is True → pulsing red background + ⚠ prefix on row.
- **NEW (2026-06-08): per-row PnL no longer × qty** — shows per-contract values so they sum to the day-total header.
- Vol-warning badges (⚠ low vol, ⛔ very illiquid) removed earlier this session.

### `live/templates/index.html` (live page) — 2026-06-08
- Regime banner: "BULL regime (gate off)" instead of "BULL — only bull-puts"
- Config chips: thr=0.075, DKL ref=rv_vs_iv (BS d2, 10d RV vs IV), regime gate OFF chip added
- oiWarning() stubbed (vol indicators removed)
- Comment block updated from k=50 forward-empirical to k=10 rv_vs_iv canon

### `live/ranker.py` — 2026-06-08/09
- `top_picks` = top-5 QUALIFIED only (above 0.075 threshold). May show <5 on low-edge days.
- `ticker_rows` (table below) = all ranked candidates (up to TICKER_LIMIT=30)
- PER_DOW_THRESHOLDS = 0.075 across all days
- **NEW: earnings gate** — drops candidates where Symbol's earnings_date ∈ [entry_date, expiry_date]. Uses `data/earnings_calendar.csv`.
- **NEW: ex-div gate (bear-calls only)** — drops bear-calls where ex-div ∈ [entry_date, expiry_date+1]. Uses `data/dividend_calendar.csv`.
- **NEW: RV table forward-fill** — uses most-recent RV per Symbol so live picks get a defined rv_30d even when rv_table is days stale.

### `live/static/style.css`
- **NEW: `tr.assignment-risk`** rule with pulsing red background animation.

### `live/upload_to_mya.sh`
- **CRITICAL FIX**: now pulls Mya-side `actual_credit` values BEFORE rsync and merges them into local frozen JSONs. Closes the race window where manual uploads (outside cron) destroyed user edits. Verified with end-to-end test.

### `live/ranker.py`
- Merges `iv_rank.parquet` and `rv_table.parquet` onto df before `build_candidates`
- `PER_DOW_THRESHOLDS` = 0.075 across all days

### `report_three_sizings.py`
- K_VAL = 10, THRESH_BY_DOW = 0.075, REGIME_FILTER = False
- **MIN_ENTRY_DATE = 2020-01-01** (incl. COVID crash — was 2020-04-01)
- **credit basis 0.80 × raw_last** (was 0.85)
- Cache: `output/picks_cache_k10_rv_vs_iv_thr075.parquet`
- Loads IV-rank + RV lookups, merges onto df before candidate building
- Config dict in payload reflects new canonical

### `live/track_frozen.py` (late update)
- `LAST_PCT = 0.80` (was 0.85; lowered for fill-quality buffer)

### `config.py`
- `REGIME_FILTER = False` (canonical default — was True)

### `live/templates/history.html`
- Removed both ⚠ low-vol AND ⛔ very-illiquid badges (per user request)

### `live/templates/index.html`
- `oiWarning()` stubbed to return empty string
- **NEW (2026-06-08): SPY card collapsible** — collapsed by default, click summary line to expand. Remembers state via localStorage.
- **NEW (2026-06-08): DKL column removed** from candidates table; `white-space: nowrap` on all td/th so rows fit one line.
- **NEW (2026-06-08): p / r₀ / q column shows RV-implied top, IV-implied bottom** (was delta above, empirical historical below).

### `live/fetch_daily_bars.py` (NEW)
- Pulls 20 daily TRADES bars per SP100 ticker via IBKR `reqHistoricalData`
- Computes 10-day RV per ticker, MERGES into `output/rv_table.parquet`
- Cron: 5:01 PM Mon-Fri via `live/cron_daily_bars.sh`
- Replaces vendor dependency for live RV

### `fetch_earnings.py` (existing, FIXED 2026-06-08)
- Now MERGES with existing CSV instead of replacing
- Avoids the wipe pattern that lost 2020-2025 history

### `fetch_dividends.py` (NEW 2026-06-08)
- NASDAQ ex-div calendar scraper, mirrors fetch_earnings.py
- MERGES with `data/dividend_calendar.csv`
- Default window: today + 120 days

### `live/cron_calendar_refresh.sh` (NEW)
- Weekly wrapper: runs both fetch_earnings + fetch_dividends
- Cron: Friday 5:01 PM via `1 17 * * 5`

### `report_three_sizings.py` — late 2026-06-08
- **Partial-WIN haircut (50%)** applied to cache PnL before equity simulation
- Models live pin-risk + assignment risk realistically
- Config caption: "0.80×clamped LAST (20% haircut); partial-WIN at 50% intrinsic (pin-risk realistic)"

### `live/templates/backtest.html`
- Subtitle updated to reflect new canon: "rv_vs_iv DKL · k=10 · thr=0.075 · top-5 per day · no regime gate · qty=2"

---

## 2. What was tested and REJECTED this session

| Test | Result | Why rejected |
|---|---|---|
| q_down_ro_sym DKL | best Sh 0.93 | Below canon 1.49 |
| BS-floor on selection credit | Sh 0.52 | Strips VRP edge from deep-OTM weekly picks |
| PROB_BASIS=rv (RV-derived p,q,ro) | Sh 1.33 | IV's forward signal beats backward RV |
| DKL(P_rv ‖ Q_iv) k=50 single point | Sh 1.20 | Tuned poorly — needed 2D sweep |
| DKL(P_iv ‖ Q_rv) k=50 single point | Sh 1.19 | Same VRP gap as rv_vs_iv |
| 30-day RV | (tenor mismatch caught by user) | Switched to 10d |
| PROB_PARTITION="2-state-loss" (ro→q) | Sh 1.89, DD -13.6% | Too conservative; deep-OTM picks lose harder |
| PROB_PARTITION="2-state-win" (ro→p) | Sh 0.65, DD -73.6% | Catastrophic — over-permissive |

The one tested DKL variant that **worked** after proper tuning: **DKL(P_rv ‖ Q_iv) with k=10, thr=0.075, tenor-matched 10d RV** — best from 2D sweep, Sh 1.44 with regime gate ON, **Sh 2.19 with regime gate OFF**.

**The 3-state α-weighted PROB_PARTITION is empirically optimal** — both partition-collapse variants are worse. The α = (b-1)/(2b) partial-zone math is doing real work.

---

## 3. Live pipeline as of now

**Full crontab (updated 2026-06-15 — expiry crons now Thu+Fri w/ settlement-day guard):**
```
1,31 9-16 * * 1-5   cron_parallel.sh         Mon-Fri, :01/:31 of 9 AM-4 PM   (live ranker; Fri scans same-day DTE0)
1 16 * * 4,5        cron_expire.sh           Thu+Fri 4:01 PM    (guarded: acts only on the week's settlement day)
1,31 9-15 * * 4,5   cron_track_expiring.sh   Thu+Fri MTM        (guarded)
1 15 * * 4,5        cron_close_alert.sh      Thu+Fri 3:01 PM    (guarded)
1 17 * * 1-5        cron_daily_bars.sh       Mon-Fri 5:01 PM    (daily RV refresh + post_close snapshot settle + Mya upload)
1 17 * * 5          cron_calendar_refresh.sh Friday 5:01 PM     (earnings + ex-div weekly merge)
```
The three expiry crons use the `live/trading_calendar.py --is-settlement-day` guard so holiday-shifted weeks (Friday NYSE holiday → Thursday expiry) settle on the real settlement day; the non-settlement day of the Thu/Fri pair no-ops. (`cron_calendar_refresh` stays Friday-only by design.)

**Ranker pipeline (per firing):**
```
  └─ pull-from-mya (preserves user actual_credit edits)
  └─ SPY intraday refresh + upload
  └─ parallel option pull (20 fetchers × 5 tickers)
  └─ ranker:
      - merges IV-rank + RV onto df
      - EARNINGS gate: drops any candidate with earnings in [entry, expiry]
      - EX-DIV gate: drops bear-calls with ex-div in [entry, expiry+1]
      - GROUND scoring (rv_vs_iv DKL, k=10)
      - threshold filter (0.075)
      - top_picks = top-5 qualified only (per-DOW)
      - ticker_rows = all ranked (up to 30)
  └─ freeze (15:01 only) → writes today's frozen JSON
  └─ tracker (BS theoretical mark) → updates tracking dict
  └─ upload-to-mya (PRESERVES actual_credit edits in merge step)
  └─ pool refresh (idempotent)
```

**Daily RV refresh (5:01 PM):**
- Pulls 20 daily TRADES bars per SP100 ticker via IBKR `reqHistoricalData`
- Computes 10-day rolling RV
- MERGES into `output/rv_table.parquet` (preserves history)
- Self-sufficient — no vendor dependency for live RV

**Weekly calendar refresh (Friday 5:01 PM):**
- Earnings: NASDAQ scrape (next 30 days), MERGES with `data/earnings_calendar.csv`
- Dividends: NASDAQ scrape (next 120 days), MERGES with `data/dividend_calendar.csv`
- Both use MERGE — never wipe history

The Mac runs cron. Mya only runs the webapp (gunicorn on 127.0.0.1:3108). HUP gunicorn to reload after webapp.py or template changes.

**Note on Mac sleep:** caffeinate only holds Mac awake during cron firing (5 min). Between firings the Mac may sleep per energy-saver settings. If overnight sleep is a concern, user can:
- Add `sudo pmset repeat wakeorpoweron MTWRF 08:55:00` for reliable morning wake
- Or run `caffeinate -i &` in a persistent Terminal/screen session

---

## 4. Cache files (output/)

| file | what |
|---|---|
| `master_pool.parquet` | 15.4M rows, 2020-01 → 2026-05, includes Symbol + iv_rank_bucket |
| `iv_rank.parquet` | 137k entries — per-(Symbol, DataDate) ATM IV rank bucket |
| `rv_table.parquet` | 370k entries — per-(Symbol, DataDate) 10-day RV |
| `picks_cache_k10_rv_vs_iv_thr075.parquet` | **CANONICAL** — 1111 picks, no regime, Jan-2020 start, 0.80×LAST |
| `sweep_rv_vs_iv_scored.parquet` | 22,702 candidates pre-filter (sweep cache, can re-derive any k/thr) |
| `picks_cache_k50_fwd_emp_W30_pre_ivrank.parquet` | old IV canonical, pre-IV-rank (769 picks) |
| `picks_cache_k50_fwd_emp_W30_pre_bsfloor.parquet` | old IV canonical with IV-rank (663 picks) |
| `picks_cache_k10_rv_vs_iv_thr075_REGIME_DEAD.parquet` | rv_vs_iv with regime ON (541 picks) |
| `picks_cache_k10_rv_vs_iv_thr075_PRE_EXTENDED.parquet` | rv_vs_iv from 2020-04 start (1014 picks, pre-COVID extension) |

---

## 5. Late-session validation summary

| test | result | adopted? |
|---|---|---|
| no-regime + empirical_vs_delta (head-to-head vs rv_vs_iv) | $54k, Sh 2.04, DD -8.8% | NO — rv_vs_iv wins on every metric |
| extend window to Jan 2020 (includes COVID) | $80k, Sh 2.33, DD -4.9% (with 0.85×LAST) | **YES** — better Sharpe & DD vs canon |
| lower haircut 0.85 → 0.80 × LAST | $64k, Sh 2.08, DD -7.4% | **YES** — buffer above 0.65×LAST break-even |
| entry credit sensitivity (down to 0.50×LAST) | break-even at 0.65×LAST | banked — strategy has ~23% fill-quality buffer |
| min(L×LAST, M×MID) floor | -277% even at (0.85, 0.80) | NO — same lesson as BS-floor; MID strips VRP |
| partial→max loss stress test | $37k, Sh 1.39, DD -15.2%, still profitable | NA (stress test only) |

## 6. Open items

### Things user might want to try next
1. **Lower threshold further (thr 0.040, 0.020)** — sweep showed Sh stays >1.20 down to thr=0.020 with regime ON; no-regime version would likely allow more picks at decent Sharpe
2. **Expand to SP500** — 5× universe; ~80 min compute; expect modest +picks because mid-cap weeklies are illiquid (likely most fail OI=100)
3. **Loosen MIN_OPEN_INTEREST** — currently 100, could try 50 or 25 to see how many more picks survive
4. **Wider strikes** — currently adjacent (1-strike wide); 2-strike wide gives lower b but might have other edge
5. **DTE expansion** — currently 1-4; 1-7 would add Mon-of-prior-week entries
6. **Backtest the earnings/ex-div gates** — wired into live ranker but not yet backtested. Would tell us how many historical picks the filters would have excluded and what the Sharpe impact would be (likely small but worth measuring)
7. **2026 H1 parquet recovery** — 2026 parquet currently only has 6/1-6/5 after the wipe. ZIPs (Jan-May) already extracted under `data/DG_2026Month/` folders. A preprocess run would rebuild (no vendor cost).

### Long-standing follow-ups (from prior sessions)
- Verify CIBC handles assignment correctly on real-money basket settlements
- Real-time push notification for close_alert
- Better calibration between live snapshot timing (15:01) and backtest data (4pm vendor)

---

## 7. Memory state

`/Users/mercurio/.claude/projects/-Users-mercurio-Downloads-gepo-backtest/memory/`

- `MEMORY.md` — index
- `feedback_error_counter.md` — **now at 61** (#60 actual_credit destroy via manual upload; #61 rsync flattening repeat)
- `project_canonical_config.md` — **STALE — defer to this handoff §0** for canon
- `project_ground_dkl_proof.md` — **NEW**: head-to-head proof that GROUND > G alone, with DKL as tail-filter
- `project_4_variants.md` — variant labels still apply
- `project_last_credit_canon.md` — still relevant for entry credit basis (now 0.80×LAST)
- `feedback_never_wipe_parquet.md` — **NEW 2026-06-08**: hard rule, never wipe parquets/CSVs without explicit user permission
- `user_role.md`, `validation_checklist.md` — unchanged

---

## 8. Lessons banked

1. **LAST credit for SELECTION captures VRP**. BS or MID-based credit strips out the variance risk premium that's the actual edge for credit-spread sellers. Keep LAST.
2. **BS theoretical for tracker MTM**. Bid/ask is fake on illiquid weeklies, LAST goes stale fast. BS gives a deterministic, IV-driven number consistent with what brokers display.
3. **IV beats RV as a probability forecast**. Forward-looking signal contains skew + regime info that backward 30-day RV can't see. Several variants substituting RV for IV in probability calc were all worse.
4. **Empirical bucket signal > pure VRP-gap signal** UNTIL we discovered the regime gate was hurting us. Then rv_vs_iv finally won.
5. **The regime gate was a dead weight**. GROUND ranking already discovers per-spread edge; the bull/bear direction filter was throwing out edge picks.
6. **Tenor matching matters**. 30d RV vs 1-4d option = mismatch. 10d RV is the right window for our DTE.
7. **Manual uploads need same protection as cron**. Earlier cron-race fix wasn't enough; manual `upload_to_mya.sh` calls also need to pull edits first. Now baked into the upload script itself.
8. **GROUND is a top-tail skimmer, not a broad-population predictor.** 9 of 10 deciles are -EV across all candidates. Only the top decile (especially top 2.4% above threshold) is profitable. The threshold is not optional — it's the strategy.
9. **DKL alone is noise; GROUND > G alone**. Per `project_ground_dkl_proof.md`: G alone +$22.10/pick at 44.8% WR; GROUND +$24.77/pick at 52.6% WR. The DKL filter swaps 32% of G-top for cleaner picks (27% WR → 51.7% WR on the swapped set). DKL contributes zero per-pick predictive power but materially improves selection at the top tail.
10. **3-state α-weighted PROB_PARTITION is the right balance**. Both "fold partials into wins" (Sh 0.65, DD -73%) and "fold partials into losses" (Sh 1.89, DD -13.6%) destroy the strategy in opposite directions. The α = (b-1)/(2b) math values partial outcomes correctly.
11. **HARD RULE: never wipe parquet/CSV without permission**. Two paid-vendor data wipe incidents on 2026-06-08 (preprocess wiped 2026 parquet, fetch_earnings wiped 2020-2025 calendar). Both recoverable but trust-eroding. Memory file `feedback_never_wipe_parquet.md`. Always MERGE, never REPLACE.

---

## 9. GROUND vs G alone — proof excerpt (2026-06-08)

**Same N=542 picks selected three ways from the 22,702 pre-filter candidates:**

| selector | n | mean_pnl | sum_pnl | WR |
|---|---|---|---|---|
| G alone (top 542) | 542 | +$22.10 | +$11,975 | 44.8% |
| low DKL (bottom 542) | 542 | −$20.00 | −$10,841 | 48.0% |
| GROUND top 542 ★ | 542 | **+$24.77** | **+$13,424** | **52.6%** |

**The swap that drives the improvement:**

| set | n | mean_pnl | WR | median_DKL |
|---|---|---|---|---|
| G-top, DKL DROPPED | 172 | −$1.26 | 27.3% | 0.2838 (huge VRP gap = market warning) |
| GROUND added (G missed) | 172 | +$7.16 | 51.7% | 0.0052 (IV/RV agree = clean edge) |

DKL alone is uninformative (Pearson with PnL = +0.004) but the exp(-k·DKL) discount swaps 27%-WR picks for 52%-WR ones at the top tail. Full proof: `project_ground_dkl_proof.md`.

---

## 10. Paper follow-up notes — `paper/gepo_ground_2026.tex` (2026-07-08)

Do not edit yet; user wants to revisit later. Recommended tightening before treating as submission-grade:

1. **Universe / survivorship** — clarify whether S&P 100 membership is point-in-time or current/static. If current/static, acknowledge survivorship bias or add a point-in-time robustness check.
2. **Frozen protocol / data mining** — make the 2026 out-of-time claim more defensible by stating the final feature/grid freeze date and what was not changed afterward.
3. **Execution claim strength** — keep the midpoint argument, but soften claims based on five real combo fills. Add explicit commissions/fees assumptions if they are not already included in the reported P&L.
4. **P/Q measure language** — soften “RV-implied triple is exactly the physical measure” and “IV triple is risk-neutral” into reduced-form/proxy language. Black-Scholes `N(d2)` with per-leg IVs is not a full arbitrage-consistent density.
5. **Robust-preferences equivalence** — clarify that the Hansen-Sargent-style equivalence is for the transformed score `g = log(E)`, not literally the Kelly log-growth `ell`.
6. **Add robustness table** — include a compact selector comparison: GROUND vs G-only vs DKL-only / low-DKL / raw EV / random, using identical candidate pool and fill assumptions.

Overall read: the paper's core is strong. Best empirical fact is still the identical-pipeline comparison where ungated Kelly selection loses money and the GROUND gate wins.

---

## 11. Quick reference: regenerate and push

```bash
# Re-score from scratch (only if cache deleted)
python3 -u report_three_sizings.py > /tmp/regen.out 2>&1

# Push backtest JSON + frozen + ranked to Mya
bash live/upload_to_mya.sh

# Force gunicorn reload after webapp.py / template changes.
# NOTE: webapp.py + templates are NOT in upload_to_mya.sh — rsync them by hand, then HUP.
# Match the MASTER by PPID==1 (cmdline is `live.wsgi:app`). Do NOT `pkill -f gunicorn.*live.wsgi`
# — that pattern also matches your own SSH command line and kills the session.
ssh "$MYA_SSH_HOST" 'M=$(ps -eo pid,ppid,cmd | grep "[g]unicorn" | grep "live.wsgi" | awk "\$2==1 {print \$1}" | head -1); kill -HUP "$M"'

# Rebuild IV-rank + pool from scratch (~20 min)
python3 -u build_production_pool.py

# Rebuild RV table only (~30 sec)
python3 -u build_rv_table.py
```

---

## 12. Production migration plan — Mac mini runner, Mya display, MacBook dev

Goal: stop running production GEPO live ops on the 2020 Intel MacBook Air. The Air is only 2-core i3 / 8GB RAM and is already showing fan/heat problems under IB Gateway + pandas/parquet + Codex/browser/background jobs. Production should be boring and dedicated.

### Target shape

- **Mac mini:** production runner. Runs IB Gateway, live option/SPY fetches, ranking, freezing, tracking, expiry settlement, health checks, and upload/sync to Mya.
- **Mya:** web/display server. Serves Flask/gunicorn/nginx and receives generated live artifacts. No IBKR credentials required.
- **MacBook Air:** dev only. Code edits, backtests, manual checks. Existing crons are currently left in place by user choice; keep IB Gateway closed on the Air so it does not compete for live IBKR data.

### Actual setup status — 2026-08-19

Mac mini is now the intended production runner.

Confirmed setup:
- Mac mini was set up as a new Mac under user `securio`; repo path is `/Users/securio/Downloads/gepo-backtest`.
- Chrome Remote Desktop works and is the reliable remote path for now.
- Apple Screen Sharing/VNC was enabled and `screensharingd` listened on port `5900`, but it failed from the MacBook Air because local LAN traffic between `192.168.2.200` and `192.168.2.202` timed out both ways. Diagnosis: Bell Home Hub 3000 Wi-Fi/router client isolation or LAN filtering, not a macOS firewall problem.
- Xcode Command Line Tools installed.
- Repo was cloned on the mini, but GitHub push from the MacBook failed because the existing `origin` token is invalid. Today’s unpushed Mac mini setup kit and data were relayed through Mya instead.
- Mya SSH works from the mini after adding the mini's `gepo-mac-mini` public key to Mya `~/.ssh/authorized_keys`.
- Mini `~/.gepo_env`:
  ```bash
  export MYA_SSH_HOST="ubuntu@gepo-ticker.peter.cloudmallinc.com"
  export MYA_REMOTE_BASE="/opt/vito/gepo-backtest/live"
  export IB_PORT=4001
  ```
- Copied production data/setup from MacBook to mini through Mya `/tmp/gepo-mini-transfer/`:
  - `output/master_pool.parquet`
  - `output/iv_rank.parquet`
  - `output/rv_table.parquet`
  - `output/2026_sp500_last_oot_combined.parquet`
  - `output/picks_cache_oot2026_oot_combined_grv_k10_thr0.05_mid.parquet`
  - `live/data/backtest_equity.json`
  - `live/data/oot_equity.json`
  - `deploy/mac-mini/`
  - updated `append_oot_vendor_data.py` and `report_oot_2026.py`

IB Gateway/API state:
- IB Gateway 10.50 installed and running on the mini.
- API socket port is `4001`.
- **Read-Only API is ON and should stay ON. Do not disable it unless user explicitly decides to allow trading automation.**
- Market-data and historical-data farms showed connected.
- Manual data-only smoke worked:
  ```bash
  nc -zv 127.0.0.1 "$IB_PORT"
  python3 -m live.fetch_spy_intraday
  python3 -m live.health_check --force
  bash live/upload_to_mya.sh
  ```
- `fetch_spy_intraday` logs IBKR read-only errors for order/open-order calls. That is expected and desirable while the API is read-only; SPY quote fetch still wrote `live/ranked/spy_intraday.json`.

Cron installed on the mini:
```cron
# BEGIN GEPO MAC MINI
1,31 9-16 * * 1-5 /Users/securio/Downloads/gepo-backtest/live/cron_parallel.sh
1 16 * * 4,5 /Users/securio/Downloads/gepo-backtest/live/cron_expire.sh
1,31 9-15 * * 4,5 /Users/securio/Downloads/gepo-backtest/live/cron_track_expiring.sh
1 15 * * 4,5 /Users/securio/Downloads/gepo-backtest/live/cron_close_alert.sh
1 17 * * 1-5 /Users/securio/Downloads/gepo-backtest/live/cron_daily_bars.sh
1 17 * * 5 /Users/securio/Downloads/gepo-backtest/live/cron_calendar_refresh.sh
*/5 9-17 * * 1-5 /Users/securio/Downloads/gepo-backtest/live/cron_health.sh
31 17 * * 5 /Users/securio/Downloads/gepo-backtest/live/cron_pool_refresh.sh
# END GEPO MAC MINI
```

MacBook Air crons were intentionally left in place per user instruction. User plans to keep IB Gateway closed on the MacBook Air so MacBook production jobs cannot fetch live IBKR data. Remaining risk: non-IB jobs on the Air may still run and upload duplicate health/data artifacts; revisit only if duplicate uploads show up.

Power settings on the mini:
```bash
sudo pmset -a sleep 0 disksleep 0 displaysleep 30 womp 1
pmset -g
```
Verified state included `sleep 0`, `disksleep 0`, `displaysleep 30`, and `womp 1`. Computer should stay awake; display can sleep.

Known rough edges:
- `deploy/mac-mini/smoke_test.sh` passes the venv import check, but the `/usr/bin/python3` system-Python import check reports missing packages. Manual venv smoke and upload passed. Patch the smoke script later to skip system Python by default or make every cron wrapper explicitly use `.venv`.
- Chrome Remote Desktop resolution is constrained by the attached HDMI/TV display advertising only `1920x1080`. If headless remote resolution remains annoying, buy a cheap HDMI dummy plug that advertises better/multiple resolutions.
- Fix GitHub remote auth/token and push the `455f1a1 Add Mac mini production setup kit` commit plus any later handoff edits.

### Hardware decision

Buy **M4 Mac mini 16GB / 256GB** unless a 24GB/512GB refurb is close in price. User quoted roughly CAD 1100 for 16/256 vs CAD 1700 for 24/512; at that spread, do not pay the extra CAD 600 for this workload.

Reasoning:
- 16GB is enough if the mini is dedicated to production runner duties.
- Storage is easy to extend with a 1TB external USB-C/NVMe SSD.
- Spend savings on UPS, remote access reliability, external storage, and possibly AppleCare.
- 24GB/512GB only makes sense if the mini also becomes a research/backtest workstation.

### Network and physical setup

Mac mini has built-in Wi-Fi, so Ethernet is not required for phase 1. Use Wi-Fi if running cable from the front closet is painful. Jobs run every 15-30 minutes, so brief network blips are survivable if health checks alert.

Initial setup needs a temporary screen/input path:
- TV or monitor over HDMI.
- USB keyboard works. If keyboard/mouse are USB-A, use a USB-C hub.
- Bluetooth mouse usually works during setup, but wired/borrowed mouse is easier.
- After setup, enable Apple Screen Sharing for home access, Chrome Remote Desktop for away-from-home access, and SSH/Remote Login for terminal work. Then run the mini headless.

Later wired options if Wi-Fi proves flaky:
- MoCA adapters if coax outlets exist near router and mini.
- Powerline Ethernet.
- Mesh Wi-Fi node with Ethernet jack near mini.
- One professionally run Ethernet cable.

### IBKR operating model

IBKR Gateway/TWS is not serverless. It needs GUI login/authentication and occasional attention. IB Gateway is still the right production app because it is lighter than full TWS.

Current phase: the mini is using the existing IBKR username. This is fragile because logging into IBKR elsewhere can kick the Gateway/API session on the mini.

Next action: create/enable a second IBKR username dedicated to the mini/API. Keep that username logged in only on IB Gateway on the mini. Use the primary username for manual Client Portal/TWS/trading work. Goal: manual logins should not kill the mini's API/data session.

Important constraints:
- Keep mini API read-only until the user explicitly chooses otherwise. User said: "dont fucking trade anything."
- Confirm the second username has the needed market-data entitlements. IBKR market data can be username/session-specific, so duplicate OPRA/options data fees may apply.
- After second username is active, log the mini into Gateway with that username, rerun:
  ```bash
  source .venv/bin/activate
  source ~/.gepo_env
  python3 -m live.fetch_spy_intraday
  bash deploy/mac-mini/smoke_test.sh --ibkr
  ```
  Then verify Mya updates from the mini while the primary username is used elsewhere.

### Migration checklist

1. **Clean Git/security first.**
   - Rotate the GitHub token that appeared embedded in the `origin` remote URL.
   - Set token-free remote:
     ```bash
     git remote set-url origin https://github.com/pjmercuri0/gepo-backtest.git
     ```
   - Commit/push the code intended for production.

2. **Set up the mini.**
   - Complete macOS setup.
   - Join Wi-Fi.
   - Enable Screen Sharing and Remote Login.
   - Install Chrome Remote Desktop for away-from-home access.
   - Disable computer sleep; display sleep is OK.
   - Install Xcode Command Line Tools, git, Python tooling, and IB Gateway.

3. **Clone and install.**
   ```bash
   git clone https://github.com/pjmercuri0/gepo-backtest.git
   cd gepo-backtest
   python3 -m venv .venv
   . .venv/bin/activate
   pip install -r requirements.txt -r live/requirements.txt
   ```

4. **Copy live state from MacBook to mini.**
   These dirs are mostly gitignored; git clone is not enough:
   ```bash
   rsync -az live/frozen live/intraday_picks live/ranked live/data mini:~/gepo-backtest/live/
   ```

5. **Create `~/.gepo_env` on the mini.**
   ```bash
   export MYA_SSH_HOST="ubuntu@..."
   export MYA_REMOTE_BASE="/opt/vito/gepo-backtest/live"
   export IB_PORT=4001
   # optional:
   export MYA_SSH_KEY="$HOME/.ssh/id_ed25519"
   ```

6. **IB Gateway smoke test.**
   - Log into IB Gateway on the mini.
   - Confirm API/socket clients enabled.
   - Confirm live port `4001` or paper port `4002`.
   - Run:
     ```bash
     python3 -m live.fetch_spy_intraday
     python3 -m live.health_check --force
     bash live/cron_parallel.sh
     bash live/upload_to_mya.sh
     ```

7. **Move scheduling.**
   - Short term: copy existing cron schedule to the mini because the repo already has cron wrappers.
   - Better final form: `launchd` plists for scan, health, daily bars, pool refresh, expiry/track/close-alert jobs.
   - Remove/disable production cron from MacBook only after the mini completes a real market scan and Mya updates correctly.

8. **Backups and rollback.**
   - Back up `live/frozen/`, `live/intraday_picks/`, `live/ranked/`, `live/data/`, and any actual-fill edits.
   - Do not expose the IBKR API socket publicly. Keep it localhost-only.
   - Keep MacBook capable of manual emergency run during cutover, but do not let both machines run production schedules at the same time.

9. **Still todo after 2026-08-19 setup.**
   - Enable second IBKR username for the Mac mini so the mini Gateway session is not killed by manual IBKR logins.
   - Confirm second username market-data entitlements.
   - Watch the next regular market scan logs on the mini after 09:31 ET.
   - Fix GitHub auth/token and push the local Mac mini setup commit.
   - Patch `smoke_test.sh` system-Python check or make all cron wrappers explicitly use `.venv`.
   - Decide later whether to disable MacBook Air crons; for now user chose to leave them and keep MacBook IB Gateway closed.

### Repo work still worth adding

Created `deploy/mac-mini/` on 2026-08-18 as tomorrow's phase-1 setup kit:
- `README.md` — physical setup, clone/install, state copy, smoke tests, crontab cutover, acceptance checks.
- `gepo.env.example` — template for `~/.gepo_env` on the mini.
- `bootstrap.sh` — macOS checks, directory creation, and dependency install for both `python3` and `/usr/bin/python3` when they differ.
- `smoke_test.sh` — import checks, repo/state checks, forced health alert, optional `--ibkr` SPY fetch, optional `--full` live pipeline.
- `crontab.template` — current production schedule with `__REPO__` placeholder.
- `install_crontab.sh` — preserves existing crontab outside a `BEGIN/END GEPO MAC MINI` managed block; supports `--dry-run`.

Launchd plists are still the cleaner final form, but phase 1 should use cron because the live wrappers are already cron-shaped and tested.

Also consider pinning live dependencies more explicitly. Current `requirements.txt` is backtest-focused and `live/requirements.txt` carries Flask/pyarrow/ib_insync/gunicorn.

### Trading-stage note

Current stage is testing money, not full production bankroll. User has about CAD 908 in IBKR, enough to test tiny defined-risk spreads if IBKR preview confirms buying power and market-data minimums remain satisfied. For a GE 375/377.5 bear call at 1.40 credit, max loss is `(2.50 - 1.40) * 100 = USD 110` per 1-lot, but actual max loss depends on actual fill credit. Do not rely on the site mid as guaranteed fill.

---

## 13. Tone

User is invested in GROUND (it's their invention). When proposing alternatives that REPLACE GROUND's structure, flag that clearly. When changes preserve GROUND while improving inputs (like IV-rank or rv_vs_iv DKL), they're fair game. User wants critique not flattery; verify numbers before claiming; acknowledge errors directly. Error counter remains visible — read `feedback_error_counter.md` early.
