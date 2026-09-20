# GEPO session handoff — 2026-06-10 (canon) · 2026-07-08 (live-ops) · 2026-07-17 (IBKR/health ops) · 2026-08-19 (Mac mini cutover) · 2026-08-24 (OOT/history repair) · 2026-09-01 (cross-machine integration) · 2026-09-03 (euro lane) · 2026-09-11 (assignment monitor + IV skew + delta canon)

**Last updated:** 2026-09-19 EDT, evening (**§0.57 is the current canon**; §0.56 folded in; **§0.55 is the latest live-ops state** — live-quote and mark corrections, commission zeroed). Current production is D_ent with fitted 0.55-delta shorts (0.50-0.60 band), `k=4`, `GROUND >= 0.005`, own-gap P_real drift, and model-credit ranking. Direction/selection: **two-sided and symmetric on the 100d SMA, LIVE and backtest (`config.REGIME_BULL_ONLY = False`, commit `0c79d6a`, 2026-09-19 evening): bull puts when the prior completed SPY close is above its 100-session SMA, bear calls below, cash if unknown. Live still applies ONE GROUND threshold (0.005) and ONE parity rule to both sides; the backtest bear sleeve uses 0.001 / mirrored parity > 0.25 / cap 5.** Execution remains quote >= 1.00x model, with a 1.04-1.10x target. The 2026-09-11 20-delta canon and the later two-sided/top-5 variants are superseded. The Mac mini remains the production runner and must pull GitHub `main` for this change.

## The strategy in three sentences (user, 2026-09-15 — verbatim, do not reword)

> Bet the direction the stock is predicted to drift, when that prediction beats the market odds.
> Favor names where the market's view is high-entropy / low-risk — that's where premium is richer.
> Take bets where the fill yields positive expected value.

(Mapping to the code: sentence 1 = P_real vs Q via Kelly EV; sentence 2 = the exp(−k·D_ent)
discount, D_ent = ln3 − H(Q); sentence 3 = the ≥1.00× model execution gate.)

## 🛑 START HERE — CURRENT OPERATING STATE

This block and the two safety/workflow blocks immediately below it are the authoritative instructions for current work. **Do not infer current tasks from the historical archive later in this file.**

- GitHub `origin/main` is the source of truth. At this update, the MacBook and Mac mini histories have been fully reconciled and pushed; there are no seven-commit or eleven-commit transfers left to perform.
- GitHub SSH authentication works on both machines. Any later statement that GitHub authentication is broken, a token must be fixed, or commits still need to be transferred is historical and obsolete.
- The Mac mini at `/Users/securio/Downloads/gepo-backtest` is the production runner. The MacBook is the development machine.
- Production strategy canon is the **D_ent + bull/parity overlay** canon of 2026-09-16 (§0.54):
  `DELTA_TARGET=0.55`, `DELTA_MIN=0.50`, `DELTA_MAX=0.60`, `DKL_K=4.0`,
  `GROUND_THRESHOLD=0.005` (§0.43, 2026-09-15; was 1.0 / 0.01), `PROB_BASIS="realized"`, `DKL_REFERENCE="entropy_uniform"`
  (that last name describes the behaviour but is NOT a constant in `ent_canon.py` — §0.37).
  `TOP_N=10`, `REGIME_FILTER=True`, `REGIME_BULL_ONLY=True`, `REGIME_LAG_SESSIONS=1`,
  `PARITY_MIN_PCT=0.12` (strict `>`). **LIVE SELECTION: rank on MODEL credit, execution gate = IBKR quote >= 1.00x model**
  (`LIVE_SELECTION_CREDIT="model"`, ranker gates on `tgt_walkaway_credit`). Quoted-mid ranking and the
  1.04x gate are OFF. Basis: §0.38 replay, model/1.00x = 118 trades $950, best per-trade and lowest DD.
  Execution targets (final, 2026-09-13 late): **min 1.00x model credit = execution gate, target
  1.04-1.10x** (`7a1f0f4` + this commit; the 1.04x floor of `87901c2` lasted a few hours).
  (The 20-delta canon of 2026-09-11, §0.20, lasted one day and is superseded.)
- The web app has an `actuals` tab for manually tracked real trades. It is populated only by pressing `+` on History or Snapshots rows; it does not place trades and does not require IBKR API write access.
- The previously pending `report_oot_2026.py` SPY-calendar fallback and `live/freeze_snapshot.py` 15:31 top-up fixes are integrated in `main` and deployed in the Mac mini checkout. Do not redeploy them as pending patches.
- IBKR API access must remain read-only. Never place trades or enable trading access.
- The main remaining production improvement is a dedicated second IBKR username for the Mac mini, with market-data entitlements verified, so manual logins do not terminate its Gateway/API session.
- **CURRENT CANON is §0.57 (2026-09-19 evening)**: full-SP100 frame from `build_frame.py`, width <= 2.5,
  risk-sized $200/pick, dollar-P&L Sharpe as the headline, start 2020-08-01, bankroll $20k. The old 1.52 was
  an accidental 20-delta chain gate (see §0.57). Payload-side only; `live/ranker.py` unchanged.
- **SUPERSEDED §0.56 (2026-09-19)**: the §0.54 cell PLUS a two-sided regime that is
  symmetric on the 100d SMA (bull puts above, bear calls below, no dead zone) and ex-dividend +
  earnings gates on BOTH sleeves. Backtest/OOT payloads only -- `live/ranker.py` is still bull-only.
- **SUPERSEDED canon §0.54 (2026-09-16)**: 50-60 delta by fitted delta, G on P_real,
  D_ent = ln3 − H(Q_bs), k=4, threshold 0.005, bull-regime bull puts only, parity >12%, top 10/day.
- **OLD (superseded) SCORING CANON §0.21 ("52:10")**: DKL = D(P_emp‖Q_iv) with outcomes
  counted directly from realized spreads, keyed (ticker, $width) with pooled
  fallback, 52-expiry causal window, k=10. `rv_vs_iv` is dead. The LIVE path is
  NOT yet switched over — see the "NOT DONE" note at the end of §0.21.
- **On the MACBOOK: test delta-matched P_real** (§0.41) — user's idea, not started.
- **On the MACBOOK: check `live/snapshots/` for 2026-05-27..08-19 chains and run the
  IBKR replay** (§0.38) — DONE on the mini via the staged chains; see §0.38/§0.39. The mini only has chains from the 2026-08-19 cutover, and over
  those 12 days the NEW canon LOST to the old one (-$21 vs +$1,350) on a credit/width of
  0.503 vs the backtest's 0.543. This needs a longer window before the canon is trusted live.
- **CANON CELL CHANGED 2026-09-15 (§0.43): k=4, thr 0.005, P_real on full-session closes.** Mini
  picks it up on `git pull` (ranker reads `ground.DKL_K` / `config.GROUND_THRESHOLD`). Published:
  IS 4,552 tr / $30,655 / Sh 1.37 / DD -16.1%; OOT 594 / $4,372 / 2.62 / -4.2%. OOT is no longer a
  clean holdout for this cell (it was used to confirm it).
- **ON THE MAC MINI: seed the IBKR close store (§0.42).** `live/closes.py` now reads ONLY
  `output/ibkr_closes.parquet`; until `python3 -m live.fetch_ibkr_closes --years 2` has run
  there, live P_real is computed on snapshot prints alone (the ranker prints a WARNING).
  The published IS Sharpe 1.35 was computed on a sparse close series; on a full-session
  series the same canon is 1.20 / DD -15.9%. Delta-matched P_real (§0.41) tested: no gain.
- **Old canon vs D_ent on the same fill basis (§0.39):** repriced at m x model credit the old
  canon has no in-sample edge at fair value and loses to D_ent at every multiple; OOT D_ent wins
  at 1.08x, ties at 1.04x, trails at 1.00x. D_ent stays deployed; the achieved live fill/model
  ratio and the May-Aug replay decide it.
- **Run `research/dkl_2026_09_13/ablate_k.py` on the MacBook** (§0.37). It is the only real
  test of whether the D_ent penalty earns its place; it needs `featATM6.parquet`, which is
  not on the Mac mini.
- **CANON CHANGED 2026-09-16 (§0.45): own-gap drift in P_real.** Before entry, each stock's OWN opening gap (ATR units)
  shifts every historical move behind that stock's P_real by beta_year x z x sigma x sqrt(DTE). No market average
  (user decision). Walk-forward beta per entry year in `ent_canon.GAP_FIT` (refit each January: `python3 fit_gap.py <year>`).
  Published: IS 4,561 tr / $32,456 / Sh 1.51 / DD -12.9% (pre-gap 4,552 / $30,655 / 1.37 / -16.1%);
  2026 593 / $4,591 / 2.97 / -4.0% (pre-gap $4,372 / 2.62 / -4.2%). **MAC MINI TO DO:** `git pull`,
  `deploy/mac-mini/install_crontab.sh` (09:36 + 10:06 `cron_name_gaps.sh`), then run `python3 -m live.fetch_name_gaps`
  once and confirm `live/logs/name_gaps.log` stores today's gaps. A stock without today's gap scores with zero drift.
- **Option-direction research (§0.46-§0.54):** same-strike call/put IV parity is now part of canon as a mild
  bottom-12% veto. The 2026 evaluation was used during selection and is not a clean holdout.
- **Directional win-rate work is the next research push (§0.44)**, for the MacBook: validate
  IV skew on 2020-25 (§0.19) and test a spike-reversal filter. Win rate is exactly
  delta-implied today, and +1pt of win rate is worth more than +1% of fill credit.
- **The open research task is §0.19: IV skew as a directional feature.** It is measured
  and significant on 2026 OOT and NOT yet validated on 2020-2025. Nothing in canon uses it.
- Assignment/pin monitoring was rebuilt on 2026-09-11 (§0.18). The Actuals tab shows ONE
  warning colour: yellow = pin risk. Extrinsic collapse is displayed in the Ext column but
  no longer warns. **2026-09-14 (`26ab18f`): the early-exercise channel is disabled and the
  Telegram alert file is written only for positions expiring TODAY** (Friday pin only). The
  page's daily pin highlight is unchanged. Telegram fires once per option per expiry.
- `live_config.LIVE_QUOTE_EXCHANGE = "ISE"` pins single-leg option QUOTES to ISE.
  Execution is unaffected and still SMART. Measured cost: ISE is wider than SMART on some
  names. Revert is one line.
- The European index option research lane is isolated. It writes parquets only under `output/euro_parquets/` and web payloads under `live/data/euro/`; do not add euro-only roots to `SP100_TICKERS` unless intentionally changing live production scans.
- Before editing on either computer, follow the Git workflow below: inspect status, fetch, and fast-forward. If anything is dirty, ahead, behind in both directions, or divergent, stop and explain it instead of modifying history.
- At the end of work, test, commit relevant source files, push directly to `origin/main`, and verify synchronization. Never force-push `main`.

- **Do not delete `data/DG_2025*/` (59.1 GB raw vendor data) yet.** The 2025 euro parquets are built, but a RUT/RUTW `UnderlyingPrice` anomaly is unresolved and may need the original CSVs to diagnose — see §0.17.

For detailed evidence of the completed 2026-09-01 integration, see §0.14. For the current web-app addition, see §0.15. **§0.16/§0.17 (European index options) are NOT an active work item** — that lane is parked; the strategy is equities. Everything after the **HISTORICAL ARCHIVE** divider is background, not an active checklist.

## 0.54 Bull-regime/parity canon promoted (2026-09-16)

User decision, now production canon:

- `GROUND >= 0.005`, `k=4`;
- bull puts only;
- trade only when the **prior completed** SPY close is above its 100-session SMA; bear or unknown regime is cash;
- same-strike option parity daily percentile **strictly > 12%**;
- top 10 qualified trades per entry day.

Parity formula per ticker/expiry chain is `median_K(call_IV(K) - put_IV(K))` over exact matched strikes whose
absolute delta is 0.35-0.65. The sign is fitted only from years before the entry year; it is +1 for 2021-2026,
while 2020 and missing observations are neutral at percentile 0.5. Percentiles are computed among that day's
bull-put candidates before the GROUND and regime gates. Both option rights must therefore remain in the live fetch.

The production regime lookup is strictly prior-session (`searchsorted(..., side="left")`) to avoid using the
entry day's close at 15:00. A missing or more-than-four-calendar-day-stale benchmark classification fails closed
to cash. The web banner uses this lagged gate; the separate live SPY-vs-SMA display is informational.

Generated `report_ent_canon.py` payloads (one contract, 1.08x model fill, $1.30 commission, partial profitable
outcomes haircutted 50%):

- 2020-2025: 4,280 trades, $41,044 P&L, 46.80% full wins, 54.86% profitable, 12.14% yield,
  1.30 weekly Sharpe, 34.76% CAGR, -23.61% max drawdown, 1.47 Calmar.
- 2026 through 2026-09-11: 670 trades, $6,930 P&L, 47.16% full wins, 55.37% profitable, 13.58% yield,
  3.06 weekly Sharpe, 116.47% annualized CAGR, -6.39% max drawdown, 18.23 Calmar.

The 2026 period is **not** a clean holdout: it was examined while choosing this configuration. CAGR and Calmar
for the partial 2026 window are annualized and should not be interpreted as a full-year realized return.

Implementation points: `ent_canon.py` owns parity formula/sign/percentile and canonical constants;
`spreads.py` owns lagged one-sided regime enforcement and carries the raw parity feature; `live/ranker.py` applies
the parity and execution gates; `report_ent_canon.py` generates both web report payloads; live top-N capture/freeze
uses `live_config.TOP_N_DISPLAY=10`.

## 0.55 Live-quote and mark corrections; commission to zero (2026-09-17) — CURRENT STATE

A day of defects found by comparing the site against TWS tickets and against itself. All
fixed and deployed; several were mine from earlier the same day.

### Quote pipeline — three separate faults

1. **Wrong venue.** `LIVE_COMBO_EXCHANGE` was pinned to `CBOE` (set 2026-09-01 when SMART
   returned nan). SMART now quotes **10/10** candidates to CBOE's 5/10 and is tighter or
   equal on every one. JNJ 267.5/265: SMART bid -1.25, exactly the TWS ticket; CBOE -1.57.
   Now `SMART`.
2. **Scan-stale prices.** Combo quotes take 2-3 s to arrive on a cold subscription (measured:
   12 bags -> 1 quoted in 1.5 s, 8 in 3.3 s), so a targeted requote cannot be fast and the
   page carried the scan's prices — up to 15 minutes old on a book that moves every tick.
   `live/combo_stream.py` now holds `reqMktData` open on the ranked spreads AND the open
   Actuals positions, publishes `live/ranked/combo_stream.json` every second, and rsyncs it
   to Mya every 2 s over an ssh ControlMaster socket (Mya has no IBKR). `webapp._overlay_stream`
   lays quote, spot, derived credit and `above_min` onto `/api/latest.json`; GROUND and the
   model credit are untouched. `WEBAPP_POLL_SECONDS` 900 -> 5, and the ranked label shows both
   clocks ("scan 10:16 · quotes 10:23:29 ET (live)").
3. **Most spreads have no complex-order book at all.** FCX 72/71 returned nan on SMART while
   TWS showed -0.68 / -0.29 / -0.48. Those TWS numbers are LEG arithmetic: bid = short_ask -
   long_bid, ask = short_bid - long_ask, mid = short_mid - long_mid (1.185 - 0.70 = 0.485).
   The daemon now subscribes to both legs of every streamed spread (deduped) and derives the
   quote when the bag does not tick. Coverage 18/30 -> 26/26.

**Line budget:** IBKR error 101 at 100 concurrent tickers. Open positions get a line first,
the ranked board fills the rest: `LIVE_STREAM_MAX_BAGS` 26, `MAX_SPOTS` 40.

**Not resolved:** the API's combo bag returns nan even when TWS's UI shows a book. Not
explained; the leg derivation makes it moot. Separately, TWS's displayed bid becomes the
USER'S OWN resting order once one is working, so the two will not agree while an order rests
(FCX: TWS bid -0.57 = the limit price; ours -0.74 leg-derived). Ours is fair value and should
stay that way.

### Marks — the intrinsic floor was wrong in three places

2026-09-15 floored every mark at intrinsic after ISRG 370/372.5 (both legs deep ITM) marked
$0.68 off per-leg IVs from a 3-point-wide book. **That floor is wrong whenever spot sits
BETWEEN the strikes**, because intrinsic then equals the full width — the CAP, not a floor —
and the near-the-money long leg still carries time value. ADI 365/362.5 with spot 362.27 and
one day left was forced to max loss and showed **-$106** against a true ~**-$1** (BS 1.43-1.48
across 35-45% IV).

Fixed in all three copies — `track_frozen`, the Actuals overlay, and `_frozen_history` (this
last one found only when History and Actuals disagreed). The floor now applies only when the
LONG leg is also comfortably ITM (>1% of spot), and every mark is clamped to [0, width].

**Two implementations became one.** History marked via `track_frozen._track_pick` off today's
snapshot IVs; Actuals marked off the IVs stored at entry. ABT 102/103 read 0.610 on History
and 0.395 on Actuals, flipping the sign of its P&L. Actuals now calls `_track_pick` too
(`webapp._track_from_snapshot`, process-cached), falling back to stored IVs only when the
strikes are absent from today's chain. They now agree on every overlapping row.

**A silent failure worth remembering:** that cache used `df == "miss"` as a sentinel, which
raises "truth value of a DataFrame is ambiguous" — swallowed by a bare `except`, so two
rounds of "fixed" changed nothing. Sentinels next to DataFrames must use `in`/`is`.

**Leg-derived quotes need a sanity check.** Once the bell went, leg quotes decayed and the
subtraction produced mid 0.0 (ADI, PM, MS) and mid -1.64 (XOM). A zero mark reads as "free to
close", and the Actuals week card showed **+$748** on a book that was **-$256**. A derived
quote is now published only if `short_mid > long_mid` and `0 < mid <= width`; a combo quote
only if `0 < mid <= width`. After hours 1 of 26 spreads still has a usable quote, which is the
honest answer — the rest mark on BS.

### Commission is zero everywhere (user decision)

`ent_canon.COMMISSION` 1.30 -> **0.0** ("ignore all commission everywhere"). Every book and
every live P&L reads that constant. Found while checking the tabs: **the published OOT payload
already omitted commission** — 473 of 571 WIN/LOSS rows reproduce exactly at comm=0 and none at
1.30 — so it had been inconsistent with the in-sample book rather than wrong on its own.

**OPEN: the in-sample payload still has $1.30 baked in** (4,978 trades x qty2 ~= **$12.9k**
understated). `report_ent_canon.py` must be re-run on the MacBook, where the research frame
lives; it cannot be regenerated on the mini.

### Snapshots booked the wrong basis

`snapshot_picks.FILL_FRAC = 0.80` applied to the IBKR quote — a pre-canon basis — while
Backtest/OOT book `FILL_MULT` x the smile-fit model credit, so settled snap P&L could not be
compared with either. Capture now uses `model_credit x FILL_MULT` when the row carries one and
records `entry_basis`; 209 stored picks were rebased. Older picks have no `model_credit` and
keep the legacy basis, so the snap aggregate still mixes bases for anything before 2026-09-15.

### Fill-sensitivity chip and payload labels were stale

The chip still showed the bull-only canon (1.08x -> IS $61.3k / OOT $8.7k) against a book of
$94.0k / $16.7k. Regenerated from the live payloads with no commission: **IS $107.0k / OOT
$16.7k**. `credit = model_credit x FILL_MULT` holds exactly, so OOT's missing `model_credit`
(dropped by `214a159`) is recoverable as `credit / 1.08`; the `weeks` block supplies the dated
buckets for the equity curve.

The payload `config.fill_basis` strings still read "0.80xmid" and "0.80xclamped LAST" —
pre-canon labels that survived regeneration. Corrected to the canon fill on both.

### Other live fixes today

- **Stale greeks at the open (NOT fixed — open defect).** IBKR's `AbsDelta` is computed off a
  pre-open spot, so on a gap morning strike selection picks the wrong strike: USB prior close
  59.73, opened 60.58, IBKR still reported the 60 strike at delta 0.579 when it was really
  **0.291**. The 09:30 board's model credit, targets and ratios all come in low as a result.
  Self-corrects within ~15 minutes (median |IBKR delta - recomputed| 0.034 at 09:30, 0.021 at
  09:45). **Fix would be to recompute delta from live spot and IV in the candidate builder.**
- The build step's no-LAST skip is a vendor-EOD rule: at 09:30 74% of weeklies have not traded
  (19% at 15:45), which cut the 2026-09-16 open from 94 viable pairs to 18. It now applies only
  under `last_clamped`; the live build prices on mid.
- `GEPO_MKT_DATA_TYPE` env override so an out-of-hours scan can request frozen data (IBKR
  returns -1 on every option under live data after the close). Cron still runs live.
- Live tab: `+` on every ranked row; blue/green/red `+` on live, History and Snapshots (blue =
  same option held, green = strike away from the move, red = strike followed the stock).

## 0.57 CANON: risk-sized book, dollar-P&L Sharpe, full SP100 frame (2026-09-19 evening)

User decision after the second-pass audit ("this is canon now"). Commits `f4435d1`, `6a4b174`,
`5d12ea3`, `a8498a5`. Backtest/OOT payloads and the Mya templates are live.

### The mistake that was found

The 1.52 Sharpe the old frame reported was an **accidental chain gate**, not strategy.
`research/dkl_2026_09_13/band_sweep.load_pairs()` inner-joins the pair pool to smile fits on
(ticker, entry_date, expiry_date); those fits (`is_synth` / `oot_synth`) were produced from the
**20-delta** candidate list of the 09-11 delta-20 era (d_sh in [0.10, 0.30], OTM <= 5, width <= 2.5,
both legs OI >= 100 and LAST > 0). So a chain entered the 55-delta frame only if a liquid 20-delta
wing pair existed that day. Original 2026 frame chains are a strict subset of the synth chains
(0 exceptions). It removed roughly half the ticker-days (2021: 5,134 vs 10,253) and was never chosen.

Proof: featATM8 restricted to featATM7's (ticker, entry_date) pairs -> Sh 1.59 / DD -17.8% (the old
book). Same 83 names, no restriction -> 1.17. Plus the 19 restored SP100 names -> 1.28 (they HELP,
+$49.8k). The excluded trades have the **same yield** (15.55% vs 15.54%) at **4x the max loss per
contract** ($223 vs $53 median); under fixed qty=2 that is pure dollar variance from strike spacing.
A ~90% replica of the 20-delta gate exists but the exact rule is unrecoverable (no builder for
`oot_pairs_q.parquet` / `/tmp/gepo_pairs.parquet`). The live ranker has no such gate, so the
full-universe book is the one that matches production.

### Canon (payload side)

- **Universe:** full `config.SP100_TICKERS` (99), every year; `build_frame.py` is the reproducible
  builder (vectorized, ~27 s/year; output identical to the loop version). Names whose adjacent
  strikes are > $2.5 apart drop out via the width cap (83-90 tickers/year in practice).
- **width <= config.MAX_SPREAD_WIDTH (2.5)** restored (`band_checks.py:61`); alone worth 1.17 -> 1.32.
- **Sizing: risk-sized.** `report_mid_canon.RISK_PER_TRADE = $200` of max loss per pick, qty =
  floor(200 / max_loss$), min 1, max `config.MAX_CONTRACTS`. Replaces fixed qty=2 as the
  `strategy` arm and the trade-row qty. qty1 and 1/16-Kelly arms kept.
- **Headline metric: dollar-P&L weekly Sharpe** (`*_sharpe_dollar`, every arm). Under constant
  $-risk this IS the Sharpe; %-of-equity Sharpe depends on the bankroll denominator (1.33 at $20k,
  1.49 at $100k, -> the dollar figure). Weekly/daily %-Sharpe removed from the tabs.
- **Start 2020-08-01** (`bear_regime_sweep.FRAME_START`), **bankroll $20,000** (`report_mid_canon.
  START_BANKROLL`, `bear_regime_sweep.START`). Peak capital at risk is ~$17k; at $10k the fixed-qty
  curve went to -$2,318 on 2020-07-24 and every ratio was meaningless.
- Regime symmetric on the 100d SMA; ex-div + earnings gates on both sleeves (0.56); vendor path
  resolver `ent_canon.vendor_year_parquet`; parity feature rebuilt through 2026-09-10.

### Bugs fixed in `report_mid_canon.build_payload`

- Calendar stopped at Dec 31, so late-December picks settling in January were listed in `trades`
  but never booked ($1,974 in the 2025 book). Now runs through the last settlement; trade P&L sum
  reconciles to `strategy_final - START_BANKROLL` to the dollar.
- Weekly Sharpe dropped week 1 (`pct_change().dropna()`); seeded from START_BANKROLL.
- Captions: `sizing` now states the rule and date.

### Published (risk-sized, $20k, entries 2020-08-03..2025-12-24; settlements to 2026-01-02)

| | trades | final | $-Sharpe | %-Sharpe | max DD | yield |
|---|---|---|---|---|---|---|
| Backtest 2020-25 | 4,846 | $137,464 | **1.80** | 1.44 | -29.6% | 15.5% |
| OOT 2026 (to 09-10) | 662 | $48,086 | **5.10** | 4.41 | -6.4% | 27.3% |

SPY $-Sharpe over the same window: 0.96. 2026 is **not** a clean holdout.

### Still open

- **Survivorship (audit #1):** `SP100_TICKERS` is a static present-day list applied backwards.
  No ticker first appears after 2023. Unquantified; needs historical constituents.
- **Entry price = 16:00 close** on 35,274/35,274 rows; live enters at 15:00.
- Live is now two-sided (`config.REGIME_BULL_ONLY = False`, `0c79d6a`) but does not risk-size, and applies the
  bull gates (GROUND 0.005, parity > 0.12, top-10 shared) to bear calls too. Bear-sleeve gates are backtest-only.
- Mya's checkout has local uncommitted edits (`base/history/index.html`); `backtest.html` and
  `oot.html` were rsynced directly (TEMPLATES_AUTO_RELOAD is on). Do not `git pull` there blindly.
- Frames are gitignored: `featATM8.parquet` regenerates with `python3 research/dkl_2026_09_13/build_frame.py`
  (~3.5 min), then `direction_signal_suite.build_chain_cache(force=True)`, then `report_bear_regime.py`.

## 0.56 CANON: symmetric 100d regime + ex-div/earnings on both sleeves (2026-09-19)

User decision, now canon for the Backtest and OOT payloads. Two changes plus one bug fix.

**1. The bear regime is symmetric on the 100d SMA.** `research/report_bear_regime.py`
`REGIME` goes `below_100_and_below_20` -> `below_100`, and `report_oot_2026.py` drops the
`< sma20` term from `bear_ok`. Bull puts above the prior-session 100d SMA, bear calls below
it, nothing in between.

Reason: the extra 20d condition created a DEAD ZONE (below the 100d, above the 20d) where
neither sleeve traded. That is 114 of 1,682 sessions 2020-2026 (6.8%), and **21.1% of 2022** --
a fifth of the best bear year sat in cash. `output/bear_regime_sweep.csv` at identical gates
shows the exclusion is not earning it: `below_100` takes 269 more bear trades that netted
**+$16 in total** (six cents each). It bought +0.013 combined IS Sharpe by deleting volume with
no edge, and 2026 OOT preferred keeping them. Not a validated condition either way; the simpler
gate wins on parsimony.

**2. Ex-dividend and earnings gates now apply to BOTH sleeves.** Both functions in
`research/bear_regime_sweep.py` carried `if row.spread_type != "bear_call": continue`; removed.
`report_bear_regime.main` applies `~exdiv_hit & ~earnings_hit` to `bull_pool` too.

The reasons differ by side and that matters if the window is ever tuned: a short CALL risks
early exercise into the dividend (the original 2026-06-09 rationale), a short PUT just eats the
ex-date price drop as a directional headwind. Bear calls measured 0.0% hit rate, confirming the
gate was already live there. Bull puts hit 16.6% (ex-div 10.2%, earnings 6.7%): 712 of 4,280
trades carrying **-$1,429**, replaced by 150 better ones worth +$1,689.

Calendars verified non-empty over the whole window first, since a missing join would have made
the gate a silent no-op: `output/yahoo_dividend_history.csv` 2,614 rows / 90 symbols /
2018-12-31..2026-09-15, `output/nasdaq_earnings_history.csv` 2,481 rows / 93 symbols /
2020-01-14..2026-08-26. (NOT `data/earnings_calendar.csv`, which is 2026-only.)

**3. BUG FIX -- the payloads were mislabelling their own numbers.**
`report_mid_canon.build_payload()` writes the OLD canon's captions and `patch_config` never
overrode them, so the published config said `fill_basis: 0.80xmid` and
`scoring: G_rv / rv_vs_iv DKL` while `realize()` was booking `ec.FILL_MULT x model_credit`
minus `ec.COMMISSION` on D_ent scoring. `patch_config` now restates `fill_basis`, `scoring`,
`dkl`, `gap` and `commission` from `ent_canon.CANON_LABELS`. Check this whenever a payload is
regenerated from a `report_mid_canon` base.

### Published (qty2, 1.08x model credit, no commission)

- **Backtest 2020-25:** 4,685 trades, $108,970 P&L, final $118,970, CAGR 57.3%,
  weekly Sharpe 1.52, max DD -26.7%. Sleeves: bull 3,718 / $94,484, bear 967 / $14,486.
- **OOT 2026 (through 09-11):** 626 trades, $24,538 P&L, final $34,538, weekly Sharpe 4.34,
  max DD -6.4%.

### READ THIS BEFORE QUOTING THE IMPROVEMENT

The backtest went $94,028 -> $108,970, which is **NOT** a +$14,942 strategy gain. Every one of
the 4,266 trades common to both books shifted by exactly **+$2.60** = qty 2 x the $1.30
commission. The old payload's caption already claimed "no commission" while its numbers still
had $1.30 baked in -- the exact discrepancy §0.55 flagged as pending.

```
OLD as published       4,978 tr   $ 94,028
OLD restated @ 0 comm  4,978 tr   $106,971   <- the like-for-like baseline
NEW                    4,685 tr   $108,970
  strategy change      +$1,999   (+1.9%)
  commission zeroing  +$12,943
```

**The strategy change is worth about +$2,000 on 2020-25, roughly 2%.** Real but small.

**OOT cannot be decomposed at all.** Only 61 of 571 old trades survive into the new book and
the per-trade shift is not uniform, because the old `oot_equity.json` came from the vendor
`report_oot_2026.py` pipeline with a different candidate universe (§0.55/`fac7fee`). Treat
$24,538 as a fresh number with no comparable predecessor, NOT as an improvement on $16,686.
2026 was examined while choosing this configuration and is not a clean holdout.

### Provenance change and what is NOT done

- `oot_equity.json` now comes from `research/report_bear_regime.py`, not the vendor
  `report_oot_2026.py`. This reverses `fac7fee`'s choice. The vendor path has the symmetric
  gate applied but was NOT re-run, and it still implements NEITHER the ex-div nor the earnings
  gate -- it has no dividend or earnings join at all.
- **`live/ranker.py` IS STILL BULL-ONLY.** `config.REGIME_BULL_ONLY = True` makes
  `spreads.py:168` drop every bear call at candidate build. This canon is backtest-side only;
  live trades no bear sleeve.
- **MAC MINI TO DO:** `git pull`, then `bash live/upload_to_mya.sh` (both payloads are in its
  file list). The MacBook carries `live/NOT_PRODUCTION` and correctly refuses to publish, so
  Mya does NOT have these numbers until the mini runs the upload.
- The per-side gates remain asymmetric on purpose: bull GROUND 0.005 / parity > 0.12 / top-10,
  bear GROUND 0.001 / mirrored parity > 0.25 / top-5.

## 0.20 Delta-target canon changed to 0.20 (2026-09-11) — HISTORICAL, SUPERSEDED

Production canon changed from ATM-ish 50-delta shorts to lower-delta shorts:

- `DELTA_TARGET = 0.20`
- `DELTA_MIN = 0.10`
- `DELTA_MAX = 0.30`
- `DKL_K = 10`
- `GROUND_THRESHOLD = 0.05`
- selection remains threshold-qualified top 5 per entry day

Reason: the old 50-delta target created maximum pin/assignment exposure and a structural near-50% win profile. The 0.20 target materially improved win rate, drawdown, and fill-stressed results.

Validation summary from the exact-equivalent candidate builder:

- 2020-2025, 0.20 / 0.10-0.30 at 0.80 x mid: 1,483 trades, 90.0% win rate, +$58.9k P&L, -2.58% max drawdown, weekly Sharpe 3.55.
- 2020-2025, old 0.50 / 0.35-0.65 at 0.80 x mid: 2,250 trades, 57.0% win rate, +$40.8k P&L, -5.82% max drawdown, weekly Sharpe 2.45.
- 2026 OOT, 0.20 / 0.10-0.30 at 0.80 x mid: 138 trades, 93.5% win rate, +$6.1k P&L.
- 2026 OOT, old 0.50 / 0.35-0.65 at 0.80 x mid: 182 trades, 57.7% win rate, +$3.1k P&L.

Fill stress:

- 2020-2025 0.20 stays positive at 0.70 x mid (+$47.9k) and 0.60 x mid (+$36.8k).
- 2026 OOT 0.20 stays positive at 0.70 x mid (+$5.1k), 0.60 x mid (+$4.1k), and natural (+$1.5k).
- Do not chase fills. Live orders should use disciplined limits near mid; do not treat bad fills as acceptable just because the ranker likes a spread.

## ⚠️ HARD RULE — read first

**NEVER delete, overwrite, or wipe parquet files (or any vendor-purchased CSV) without explicit user permission.**

Two incidents on 2026-06-08:
1. `preprocess_empirical.py --year 2026` REPLACED the year parquet with only Jan-Jun coverage, wiping months of vendor history
2. `fetch_earnings.py` REPLACED the earnings calendar, losing 2020-2025 historical earnings

User reaction: "I fucking hate you now I have to buy from the vendor again." Recovery was free in both cases (ZIPs already extracted, NASDAQ rescrape free), but trust was the real cost. **Before any operation that may write to a parquet/CSV that holds vendor or expensive-to-rebuild data: check if file exists, explicitly tell user "this will REPLACE/OVERWRITE/WIPE", wait for approval.** Prefer MERGE over REPLACE everywhere. Memory file: `feedback_never_wipe_parquet.md`.

## ⚠️ TWO-COMPUTER GIT / AI WORKFLOW — MacBook + Mac mini

GitHub `origin/main` is the source of truth. The user works directly on `main` and may alternate between the MacBook and Mac mini. **Never allow both machines or two AI sessions to edit concurrently. Finish and push on one computer before beginning on the other.**

At the start of every AI session, before editing anything:

1. Run `git status --short --branch` and `git fetch origin`.
2. If the working tree has no unexplained changes and local `main` has no unpushed commits, run `git pull --ff-only origin main`.
3. If there are local changes, unpushed commits, or divergence, stop and explain exactly what exists. Do **not** automatically stash, reset, rebase, overwrite, or force-push.

After completing work:

1. Test the change.
2. Commit only relevant files. Exclude virtual environments, logs, caches, credentials, profiler output, and generated artifacts unless they intentionally belong in Git. Do not use `git add .` blindly.
3. Push directly with `git push origin main`.
4. Verify local `main` equals `origin/main`, report the final commit hash, and identify anything intentionally left untracked.

When switching computers, the first instruction to the AI should be: **"Sync this computer from `origin/main` before making changes. Stop if it has local or unpushed work."** At the end of a session: **"Finish the current work, test it, commit it, push it to `origin/main`, and verify nothing remains unexpectedly uncommitted or unpushed."** Never use `--force` on `main`.



**READ THIS FIRST.** Major canonical changes over 2026-06-04 → 2026-06-10. Site / live ranker / backtest all current on the **mid-basis canon (2026-06-10)**. New strategic findings: GROUND beats G-alone (under mid basis G-alone LOSES $15.5k while GROUND makes +$41.3k; DKL split t=4.45), regime gate OFF is canonical (and the fetcher's puts-only-in-bull filter was removed 2026-06-10 — it had silently suppressed every bear call for 5 days, error #70), BS theoretical drives the live tracker mark (now floored at intrinsic-at-current-spot), qty=1 per-contract display everywhere.

---

## 0.15 Actuals tab added (2026-09-01) — CURRENT STATE

The web app now has an `actuals` tab for tracking real trades the user actually placed. This is bookkeeping only: the app remains read-only with respect to IBKR and must not place orders or require API write access.

Behavior:

- History rows have a `+` button. Pressing it copies that frozen pick into `live/actuals.json`.
- Snapshots rows have a `+` button. Pressing it copies that specific scan-time pick into `live/actuals.json`, so the user can record trades taken from any snapshot, not only the 15:01/15:45 freeze.
- The Actuals tab renders like the History table, but includes only rows explicitly added by the user. It carries through source date/time, ticker, direction, strikes, quantity, credit/max-loss, ratio, GROUND, delta, DTE, status, mark, and P&L when those fields are available.
- Rows can be removed from Actuals with the `x` button. Removal only updates `live/actuals.json`; it does not modify frozen/history/snapshot source files.
- `live/upload_to_mya.sh` preserves and merges Mya-side `actuals.json` before upload, just like it already preserves Mya-side `actual_credit` edits, so manual actual-trade records are not clobbered by a production upload.

Implementation files:

- `live/webapp.py`
- `live/templates/base.html`
- `live/templates/history.html`
- `live/templates/snapshots.html`
- `live/templates/actuals.html`
- `live/static/style.css`
- `live/upload_to_mya.sh`

Verification on the MacBook dev checkout:

- `PYTHONPYCACHEPREFIX=/tmp/gepo_pycache python3 -m py_compile live/webapp.py`
- `bash -n live/upload_to_mya.sh`
- Flask test client loaded `/`, `/history`, `/snapshots`, and `/actuals`, then added and removed a temporary actual from `/api/actuals/from_frozen/...` using `/tmp/gepo_actuals_test.json`.

Runtime data note: `live/actuals.json` is user/live state. Do not commit real user actual-trade data unless explicitly requested.

---

## 0.14 Cross-machine integration complete (2026-09-01) — CURRENT STATE

The previous divergence is resolved. Seven MacBook commits, eleven Mac mini commits, and the relevant work-in-progress changes from both machines were preserved, integrated commit-by-commit, conflict-resolved, tested, and pushed to GitHub `main`.

Current source-of-truth rules:

- GitHub `origin/main` is authoritative. Both computers must fast-forward from it before an AI edits anything; follow the two-computer workflow at the top of this document.
- The Mac mini production checkout was updated to integrated `main` and smoke-tested. Its local `.venv/` is intentionally untracked.
- The MacBook was updated to the same integrated `main`. Its local `default.profraw` and `live/notifications_archive_health/` are intentionally untracked generated artifacts.
- Safety branches remain on GitHub: `backup/mac-mini-2026-09-01`, `backup/macbook-wip-2026-09-01`, and `integration/2026-09-01`. Keep them until the integrated live system has run normally for a reasonable period.
- GitHub authentication is fixed on both machines using their respective SSH keys. The old notes saying GitHub auth is broken are obsolete.

Previously pending patches are now integrated and deployed in the Mac mini checkout:

- `report_oot_2026.py` includes the missing-SPY-calendar fallback used for the Aug OOT repair.
- `live/freeze_snapshot.py` uses ranked snapshot time for top-up labels, supports 15:31 vacant-slot top-up, deduplicates by ticker, and has a passing regression test in `test_freeze_snapshot.py`.
- The production fetch path includes readonly IB connections, bounded per-ticker fetching, connection retries/timeouts, revived earnings/ex-dividend gates, daily `spy_us_d.csv` refresh, and IBKR BAG/combo pricing.
- Cron wrappers use the shared `live/cron_env.sh` environment path. The parallel pull retains the SPY watchdog, non-production upload guard, virtualenv selection, and safer staggered fetch groups.

Integration verification completed on 2026-09-01:

- Python compilation passed for the affected live modules.
- All `live/*.sh` and `deploy/mac-mini/*.sh` files passed `bash -n`.
- `test_freeze_snapshot.py` passed under `unittest`.
- Fetcher, configuration, and combo-pricing modules imported successfully from the Mac mini production virtualenv.
- Full `pytest` was not run because `pytest` is not installed in that virtualenv; dependencies were not changed merely to run it.

Do **not** follow the old §0.13 instruction to stash the Mac mini and bring over seven MacBook commits; that recovery is complete. Do not redeploy the two patches that §0.13 calls "local" or "pending"; they are already in integrated `main` and the Mac mini checkout.

Still operationally relevant:

- The Mac mini is the intended production runner for IB Gateway, cron, ranking, tracking, settlement, and Mya upload.
- A second IBKR username for the Mac mini is still desirable so a manual login does not terminate its Gateway/API session.
- The historical 2026-08-24 15:31 miss may still be investigated in archived logs if useful, but the top-up code path itself is now integrated and regression-tested.

---

## 0.16 European index options + the vendor re-download (2026-09-03) — BACKGROUND, superseded by §0.17

**No longer the open work item** — §0.19 (IV skew) is. Everything below is state
as of 2026-09-03 and is superseded by §0.17 where they differ; nothing here is
done except where marked DONE. Retained because the product research (which
roots are European, which are AM-settled, what the vendor carries) is still the
reference for the euro lane.

### Why

The account cannot tolerate assignment. American equity options cannot be made
assignment-proof by monitoring — only European, cash-settled index options can,
because they cannot be exercised early and never deliver shares.

### What was found (all verified, not assumed)

**SPXW works, and the reason it looked dead was a filter, not the strategy.**
`report_oot_2026.py` hard-coded `df = df[df['exp_dow']==4]` — Friday expiries
only. That is a no-op for equities (they only list weekly Fridays) but throws
away four fifths of a daily-expiry index product. 2026 OOT, canon threshold
0.05, `--symbols SPXW`:

| run | final | return | Sharpe | maxDD |
|---|---|---|---|---|
| Friday-only | $10,141 | +1.4% | 0.21 | -4.9% |
| **all expiries** | **$12,214** | **+22.1%** | **1.62** | **-4.9%** |
| SPY same window | $11,065 | +10.7% | 1.21 | -8.9% |

Beats SPY on return, Sharpe and drawdown — **but on only 12 trades over 165
trading days.** Threshold sweep peaked at thr=0.04 (18 trades, +42.2%, Sharpe
2.50), which is in-sample tuning on the OOT window and should NOT be adopted.

**RUTW contributes nothing to the 2026 OOT** — absent from
`output/2026_sp500_last_oot_combined.parquet` entirely; its `master_pool` data
stops 2025-10-01. `--symbols SPXW,RUTW` returns a byte-identical result to SPXW
alone, all 12 trades tagged SPXW.

**The 2026 result is scored against the wrong expiry population.**
`master_pool.parquet` holds SPXW on Friday expiries ONLY — all 766,898 rows. So
GROUND scored daily-expiry trades using Friday-expiry empirical probabilities.
This affects the number above, not just future work.

**2020-2025 cannot be backtested from what is on the Mac mini.**
`output/2020_sp500_last.parquet` … `2025_sp500_last.parquet` are absent, and
`master_pool.parquet` cannot substitute: its schema is `Symbol, DataDate,
ExpirationDate, DTE, putcall_norm, abs_delta, ImpliedVolatility, itm,
delta_bucket, iv_capped, iv_rank_bucket` — **no BidPrice, AskPrice, LastPrice,
StrikePrice or UnderlyingPrice.** It is the empirical outcome lookup, not
pricing data. Do not attempt to synthesise credits from its IV column: `config.py`
records that BS/MID-based credit strips out the variance risk premium that is
the strategy's actual edge, so such a backtest would be meaningless.

### Vendor: the daily-expiry data exists and always did — DONE, verified

Current vendor is **Discount Option Data** (`discountoptiondata.com`), files
arrive as `DG_YYYYMMDD.zip` (`analyze_chain.py:13`). Their public sample
`Content/SampleData/DG_20220301.zip` was downloaded and inspected on
2026-09-03. Columns match what `analyze_chain.py` already parses. Root coverage
in that one day:

| root | present | rows | expiry weekdays |
|---|---|---|---|
| SPXW | yes | 12,424 | Mon 1,252 · Tue 716 · Wed 1,694 · Thu 2,496 · Fri 6,266 |
| RUTW | yes | 6,498 | Mon 640 · Tue 406 · Wed 1,204 · Thu 492 · Fri 3,756 |
| SPX | yes | 6,982 | Thu 854 · Fri 6,128 |
| NDX | yes | 7,298 | Thu 742 · Fri 6,556 |
| OEX, VIX, SPY, QQQ, IWM, RUT | yes | — | — |
| **XSP, XND, MRUT** | **NO** | 0 | — |

**The Friday-only limitation is OUR preprocessing, not the vendor.** SPXW and
RUTW daily expiries were in the vendor data in 2022 and were filtered out on
ingest. So the daily-expiry result IS validatable across 2020-2025.

Pricing seen 2026-09-03: **Bundled 2020-2025 with Greeks $149** (4,552 symbols,
439M rows, 5.1 GB); all years 2005-2026 with Greeks $295. Vendor FAQ states
"data includes all option expiration dates".

### Current euro scaffold — DONE 2026-09-03

This was added as an isolated lane in the same repo. It does not replace the
existing GEPO backtest/live pipeline.

- `config.EURO_INDEX_ROOTS` lists the cash-settled index roots to research:
  `SPX, SPXW, XSP, RUT, RUTW, MRUT, NDX, NDXP, XND`.
- `preprocess_euro_parquets.py` reads the vendor raw files and writes only
  `output/euro_parquets/<year>_euro_last.parquet` and
  `output/euro_parquets/<year>_euro_expiry.parquet`.
- `build_euro_pool.py` reads only `output/euro_parquets/*_euro_last*.parquet`
  and writes only `output/euro_parquets/euro_pool.parquet` and
  `output/euro_parquets/euro_iv_rank.parquet`.
- `report_euro_backtest.py` reads those euro parquets and writes
  `live/data/euro/backtest_equity.json`; its picks cache is also under
  `output/euro_parquets/`.
- All euro parquet writers refuse to overwrite an existing parquet. Use
  `--suffix` or `--cache-suffix` for a new run.
- The Flask app supports `GEPO_APP_PROFILE=euro`; in that mode the Backtest and
  OOT tabs read `live/data/euro/backtest_equity.json` and
  `live/data/euro/oot_equity.json`, and the QR page points at
  `https://gepo-euro-backtest.peter.cloudmallinc.com/` unless `GEPO_SITE_URL`
  overrides it.
- Do not depend on resolving the public domains from inside Codex. If working
  on the server, use the local source checkout such as
  `/opt/vito/gepo-euro-backtest` or `/opt/vito/user/apps/gepo-euro-backtest`.

Suggested commands after a vendor year is uploaded:

```bash
python3 preprocess_euro_parquets.py 2025 --dry-run
python3 preprocess_euro_parquets.py 2025
python3 build_euro_pool.py
python3 report_euro_backtest.py --years 2020,2021,2022,2023,2024,2025
```

If a parquet already exists, do not delete it. Re-run with a suffix:

```bash
python3 preprocess_euro_parquets.py 2025 --suffix v2
python3 build_euro_pool.py --suffix v2
python3 report_euro_backtest.py --pool output/euro_parquets/euro_pool_v2.parquet --iv-rank output/euro_parquets/euro_iv_rank_v2.parquet --cache-suffix v2
```

### Remaining euro task — in this order

1. **Ingest the re-downloaded vendor files.** Drop the monthly zips into
   `data/DG_YYYYMonth/` (e.g. `data/DG_2022March/`) — `preprocess.py:28` builds
   that path as `DG_%Y%B`. For the euro lane, use `preprocess_euro_parquets.py`
   to produce `output/euro_parquets/YYYY_euro_last.parquet` for 2020-2025.
   **HARD RULE applies: these are vendor-purchased files. MERGE, never wipe.**

2. **Build the euro empirical pool WITHOUT the Friday filter.** Use
   `build_euro_pool.py`, not `build_production_pool.py`. The output is
   `output/euro_parquets/euro_pool.parquet`.

3. **Backtest the euro roots, 2020-2025, all expiries, canon threshold 0.05.**
   This is the out-of-sample test the 12-trade 2026 result needs. Do not adopt
   thr=0.04 on the strength of the 2026 sweep. Start with `SPXW,RUTW`; expand
   to NDX/NDXP/XND/XSP/MRUT only when the uploaded data actually contains them.

4. **Regenerate the euro Backtest tab.** Use `report_euro_backtest.py`; it writes
   `live/data/euro/backtest_equity.json`. Do not replace
   `live/data/backtest_equity.json` for the main app.

5. **Check whether XSP appears in later years.** Absent from the 2022 sample.
   XSP is the product to TRADE (1/10th SPX, ~775 vs SPX ~7,754, fits a 16.3K
   account); SPXW is the product to TEST. Economics transfer exactly — same
   index, same European cash settlement, only the notional differs.

### Tooling already in place — DONE

- `report_oot_2026.py` gained `--symbols`, `--out`, `--thr`, `--any-expiry`.
  All four retag BOTH the output path and the picks cache, so a subset run can
  never overwrite the published SP100 curve or reuse the wrong cache.
- `live/fetcher.py` handles index underlyings: `Index` contract on the index
  exchange rather than `Stock` on SMART, chain matched on the index exchange,
  and `tradingClass` set (SPX monthlies and SPXW weeklies share the SPX index).
  SPXW and RUTW had been in `SP100_TICKERS` since they were added and had failed
  on EVERY scan with "No security definition has been found ...
  Stock(symbol='SPXW')".
- `live_config.LIVE_INDEX_ROOTS` holds all six roots, probed live against the
  Gateway 2026-09-03 14:19. **Exchanges are not guessable:**

  | root | underlying | exchange | qualifies | spot | chain |
  |---|---|---|---|---|---|
  | SPXW | SPX | CBOE | yes | 7,754.08 | 42 exp / 744 strikes |
  | XSP | XSP | CBOE | yes | 775.38 | 47 exp / 519 strikes |
  | RUTW | RUT | **RUSSELL** | yes | 2,966.53 | 25 exp / 342 strikes |
  | MRUT | MRUT | **RUSSELL** | yes | 296.65 | 19 exp / 225 strikes |
  | NDXP | NDX | NASDAQ | yes | **no data** | 35 exp / 463 strikes |
  | XND | XND | NASDAQ | yes | **no data** | 27 exp / 198 strikes |

  RUT/MRUT do NOT qualify on CBOE. NDXP/XND need a Nasdaq index market-data
  subscription the account does not hold — everything else about them works.

- **None of these are in `SP100_TICKERS` beyond SPXW/RUTW.** Adding one changes
  what the live scan trades. Do not enable without deciding to.

### Do not

- Do not trade this live on the 2026 result. 12 trades, one instrument, scored
  against the wrong expiry population, no out-of-sample validation.
- Do not use SPY/QQQ/IWM as the "European" universe. They are American-style,
  physically settled ETF options — the exact assignment risk being eliminated.
- Do not adopt thr=0.04 without out-of-sample confirmation.

---

## 0.17 2025 euro parquets built + first canon backtest (2026-09-03) — CURRENT STATE

### ⚠️ DO NOT DELETE THE 2025 RAW VENDOR DATA YET

User asked whether `data/DG_2025*/` (59.1 GB, 261 trading days) can be deleted now
that the parquets exist. **Not yet** — see the unresolved RUT anomaly below.
The HARD RULE at the top of this document applies: this is purchased vendor data.

### What was built

`output/euro_parquets/` (gitignored — regenerate, never expect it from a clone):

- `2025_euro_last.parquet` — 268 MB, **8,788,608 rows**, 22 columns, 260 trading
  days, 2025-01-01 → 2025-12-31.
- `2025_euro_expiry.parquet` — 608 rows. NOTE: the build logged **192 missing
  expiry-day spots, filled from the latest prior spot**. For a daily-expiry
  product that is a real approximation feeding outcome labelling; review before
  trusting settlement-dependent results.
- `euro_pool.parquet` / `euro_iv_rank.parquet` — v1, pre-IV-fix, kept.
- `euro_pool_v2.parquet` / `euro_iv_rank_v2.parquet` — **use these**, 361,605 rows.

Universe is the 5 genuinely European cash-settled roots present in 2025:
SPXW 4,273,918 · SPX 2,228,396 · NDX 1,134,236 · RUTW 924,990 · RUT 227,068.

**Excluded on purpose:** `OEX` is cash-settled but **AMERICAN-exercise**, so it can
be assigned early and fails the assignment-proof requirement (its European twin
XEO is absent from the vendor data). `VIX`/`VIXW` are European cash-settled but
settle to an SOQ of VIX futures; `rv_vs_iv` DKL would be comparing vol-of-vol.
**Absent from all 12 months of 2025:** XSP, XND, NDXP, MRUT, DJX, XEO. This
answers §0.16 item 5 for 2025: **XSP is not in the vendor data**, so SPXW is the
only way to test and XSP is traded on transferred economics.

Daily expiries confirmed retained end-to-end: **37.8% of parquet rows are
non-Friday** (Mon 7.5 / Tue 9.7 / Wed 9.3 / Thu 11.4 / Fri 62.2%).

### First canon backtest — thin, do not act on it

`report_euro_backtest.py --years 2025 --symbols SPX,SPXW,NDX,RUT,RUTW` at canon
k=10 / thr=0.05, on `euro_pool_v2`:

- **21 picks**, total +$118.40/contract, mean +$5.64, final **$10,237 (+2.4%)** qty=2.
- 8 of 21 picks (38%) expire Mon/Wed/Thu — the no-Friday-filter change works in
  the backtest, not just in the parquet.
- **All 21 picks are SPXW. SPX, NDX, RUT, RUTW produced zero.**

### Three findings that gate the next step

1. **RV gap (the reason 4 of 5 roots scored nothing).** `output/rv_table.parquet`
   contains only SPXW (258 days) and RUTW (139 days) — **no SPX, NDX or RUT at
   all** — because those two were the only index roots ever in `SP100_TICKERS`.
   The euro pipeline only *looks up* `rv_table`; it never derives RV.
   **This is recoverable without the raw CSVs:** 10-day RV derived from the
   `UnderlyingPrice` column retained in the parquet reproduces the production
   table **exactly — corr 1.0000, means identical to 4 dp** for both SPXW and
   RUTW. Next step is a euro RV table under `output/euro_parquets/`; do **not**
   merge into `output/rv_table.parquet`, which is live production data.

2. **IV = 0 on 12.8% of rows** (vendor could not solve those strikes; encoded as
   zero, not real 0% vol). Left in, a fifth of the pool piles on one point and
   collapses the IV quantile edges, so `empirical_runner.build_window_tables`
   raises `ValueError: Bin edges must be unique: [0.0, 0.0, ...]`. Filtered in
   `build_euro_pool.py` only — `empirical_runner.py` is shared with the SP100
   production pipeline and was deliberately not touched.

3. **UNRESOLVED — RUT/RUTW `UnderlyingPrice` looks corrupted.** Derived 10d RV is
   ~0.80 annualised (max 7.48) against SPX 0.15 and NDX 0.20, and the spot range
   runs 1,760–6,638 when the Russell 2000 traded roughly 2,000–2,450 in 2025.
   Investigation was stopped mid-way. **This also means the production
   `rv_table.parquet` carries the same bad RUTW values** (corr 1.0000 confirms
   they match), so it is a live-system issue, not only a euro-lane one.
   Resolve this before deleting the 2025 raw vendor files, since diagnosis may
   need the original CSVs.

### Tooling changes made this session

- `preprocess_euro_parquets.py`: retains the **full greek set plus liquidity** —
  added `Rho`, `Volume`, `AskSize`, `BidSize` (only `OptionKey` omitted, being
  fully derivable). Added a grep text prefilter before pandas: the symbol filter
  used to run *after* parsing all ~5,641 symbols, and half the corpus (the 261
  `OData1` files, A–KZR) cannot contain any index root. Options pass 17:00 → 8:54
  (1.9x). The expiry pass is unchanged at ~7:40 because it reads only 4 columns
  and was never parse-bound. Verified byte-identical row counts vs the old path.
- **`--dte-max` defaults to 8 and must be overridden.** SPX/NDX list Thu/Fri
  expiries at DTE 18+, so the default silently yields near-zero rows for them.
  Use `--dte-min 0 --dte-max 4000`.
- `build_euro_pool.py`: drops non-positive IV from both the pool and the IV-rank
  seed (see finding 2).

---

## 0.22 DKL research 2026-09-12/13 — WHAT WAS TRIED AND WHY IT FAILED

**Read this before attempting DKL work again.** A full session was spent trying to
make `exp(-k*DKL)` earn a positive k at delta-20. Eleven formulations were tested.
None survived verification. The failures are characterised below so they are not
repeated.

### State of the tree — IMPORTANT

Uncommitted edits are sitting in the working tree:

- `ground.py` — `DKL_REFERENCE = "empirical_vs_iv"`, `PROB_BASIS` touched, new branch added
- `config.py` — `MIN_CREDIT_RATIO = 0.0`, `MAX_SHORT_OTM_PCT = 5.0`, `MAX_SPREAD_WIDTH = 2.50`
- `spreads.py` — OTM cap and width cap enforced in `_build_spread`

Mya is still serving the OLD `$77,417` backtest numbers, which this session showed
are overstated. Decide whether to commit, revert, or cherry-pick before trading.

### The strategy findings that DID hold (independent of DKL)

| finding | evidence |
|---|---|
| 30% of canon's gated picks had NO BID on the short leg | vs 2.2% ungated; they supplied 31% of backtest P&L |
| `MIN_CREDIT_RATIO=0.30` is a contamination magnet | gated picks averaged 2.91x fair value; 13x enriched for zero-bid |
| Vendor IV inflates far-OTM strikes | median IV 1.40 at >8% OTM vs 0.19 at <1%; delta is derived from it, so a worthless strike reads ~0.19 delta |
| `MAX_SHORT_OTM_PCT=5.0` fixes it | drops 9% of candidates, removes 92% of IV>1.0 rows |
| Band 0.15-0.25 beats 0.10-0.30 / 0.22-0.30 / 0.30-0.40 | Sh 2.70 vs 2.61 / 1.88 / 0.78 |
| Delta overstates loss probability | d_long implies 10.7%, realised 8.37% |
| EMPIRICAL-fair credit >> delta-fair credit | IS $100,013 / Sh 4.23 / DD -4.7% vs $47,305 / 2.02 / -9.9% |
| Win probability = 1 - d_short is well calibrated | predicts 79.7%, realised 81.7-83.9% |
| Break-even fill is 0.78x fair; real fills are 1.08x | from 19 actual trades on the Actuals tab |
| Drawdown is CORRELATED-week risk, not per-trade | p99 week has 45% losers; independence predicts 0.2 such weeks, observed 1 |
| The GROUND threshold is inert | thr 0 -> 0.02 changes nothing; OOT rows byte-identical |
| Window N=13 is disqualified | DD -8.5% vs -4.0% at N>=26 |

### The eleven DKL attempts

| # | DKL(P\|\|Q) | outcome | why |
|---|---|---|---|
| 1 | `D(P_rv \|\| Q_iv)` (canon) | k>0 loses money monotonically | RV-implied triple SATURATES to (1,0,0) on 51% of candidates; corr(DKL,pnl)=+0.142, so the penalty discounts the VRP edge, not risk |
| 2 | one-sided `D(P_rv\|\|Q_iv)` | inert | fires on 14%; IV exceeds RV ~96% of the time so "reality worse than priced" is structurally rare |
| 3 | `D(Q_cert \|\| P_emp) = -ln(p_emp)` | cannot beat k=0 | `p_emp` is already inside G — double-counting, can only dilute |
| 4 | #3 x severity `(max_loss/width)` | same | `sev` spans only 0.824-0.834; contributes no discrimination. Magnitude belongs in `b`, already in Kelly |
| 5 | `D(P_short \|\| P_long)` ambiguity | right properties, too small | corr(EV)=+0.013 (genuinely orthogonal!) but median 0.017 nats = 3% haircut; k-response was noise |
| 6 | `D(P_emp \|\| Q_target)` | ranks risk, still loses | loss% monotone 6.87->11.93 (IS) and 7.24->12.94 (OOT) — a REAL risk measure — but corr(EV)=-0.45, so the penalty strips more EV than risk |
| 7 | `D(P_emp \|\| Q_delta)` full | redundant | corr(EV)=+0.467 |
| 8 | signed versions of #7 | inert | zero on 83-94%; delta overstates risk so the signed branch rarely fires |
| 9 | direction-split `D(P_dir \|\| P_other)` | conceptually wrong | unsigned — large when this side is SAFER as well as riskier |
| 10 | fragility `D(Q_shock \|\| P)` / `D(P_shock \|\| Q_delta)` | fails verification | k=2 is an isolated spike (OOT 5.33 -> 5.90 -> 5.11); loss% quintiles not monotone. Also 0.86-0.99 correlated with the other shock variants — one signal, not several |
| 11 | PIT `D(P_binned \|\| uniform)` over returns | OOT only | solves the magnitude problem (median 0.090 nats, corr(EV)=-0.005) but costs $9k and worsens IS drawdown to -7.5% |

A systematic search over **72 ordered distribution pairs x 5 k values x 2 windows
(360 cells)** found 29 that beat k=0 on Sharpe in both windows. Every one either
fails a monotonicity check or is `iv|unif`.

### The ONE thing that survived: maxent reference

`D(Q_iv \|\| uniform) = ln3 - H(Q_iv)` — negative entropy, essentially the
framework's original `maxent_ro` reference. At k=16:

| | IS | OOT |
|---|---|---|
| final | $95,627 (base $99,923) | $15,277 (base $14,739) |
| Sharpe | **+4.284** (base 4.236) | **+5.990** (base 5.377) |
| MaxDD | **-3.87%** (base -4.65%) | -1.88% (base -1.94%) |

OOT Sharpe rises monotonically k=8->16 (5.380, 5.600, 5.871, 5.990) and
`corr(EV)=+0.009`. `D(Q_delta\|\|uniform)` gives nearly identical numbers, which is
a robustness signal — two independent references agreeing.

**BUT**: penalising `ln3 - H` means REWARDING entropy, and the loss-rate quintiles
run 9.27, 9.77, 9.33, 8.80, **7.13** — high DKL is SAFER. So it improves Sharpe by
selecting higher-entropy (closer-to-money, richer-premium) trades, NOT by avoiding
risk. It is a return enhancer wearing a risk measure's clothes. Do not describe it
as risk reduction without resolving this.

### Why nothing worked — the structural argument

1. **Unsigned divergences measure disagreement, and in this market disagreement is
   MISPRICING, which is edge.** Penalising it removes profit.
2. **Signed divergences have nothing to fire on.** IV and delta both overstate
   per-trade risk (delta: 10.7% implied vs 8.37% realised), so "reality is worse
   than the model" is rare by construction — 10-17% of candidates.
3. **Risk and EV are coupled** (`corr = -0.45`). Any multiplicative penalty on risk
   removes proportionally more EV than the risk it avoids.
4. **At delta-20 the outcome triple is nearly degenerate.** p_emp ~ 0.845, and the
   triple carries 0.529 nats against a ln3 = 1.099 maximum — **48% of available
   entropy**. A KL divergence is bounded by the entropy of what it compares, so
   every 3-state divergence here is capped around 0.02 nats and cannot move a
   top-5 selection. **This is the deepest reason, and it is delta-dependent.**
5. **Putting the empirical triple into G closes the gap DKL exists to fill.** With
   `PROB_BASIS="empirical"`, G already knows reality, so there is no model error
   left to correct. With G on the model instead, EV collapses to ~0 because the
   delta-derived credit IS the EV=0 price under the delta triple.

### The most promising untested direction

Point 4 is delta-dependent and therefore testable: **re-run the DKL sweep at
DELTA_TARGET=0.50**, where the published k=10 result was obtained. At 50-delta
p ~ 0.47, the triple is near maximum entropy, and divergences have room to be
large. If k>0 earns its place there and not at 20-delta, the result is a clean
boundary condition — *the entropic discount is load-bearing near the money and
degenerate in the wings* — which reconciles the paper with this session rather
than contradicting it. That test was never run.

### Artifacts

Research scripts live in the session scratchpad (not committed). Reusable caches:
`/tmp/gepo_pairs*.parquet` (vectorised candidate pairs — 4 min for 6 years vs
264 min for the per-band loop), `/tmp/srch_{IS,OOT}.parquet` (96k/10k candidates
with all 72 pairwise divergences precomputed). `output/spread_outcomes.parquet`
(127,666 realised spreads) and `spread_triple.py` ARE committed.

---

## 0.21 52:10 canon — DKL rebuilt on measured outcomes (2026-09-12) — CURRENT CANON

**This supersedes the DKL half of canon. `rv_vs_iv` is dead.**

    GROUND = (exp(G) - 1) * exp(-k * DKL),  k = 10
    DKL    = D(P_emp || Q_iv)
    P_emp  = WIN/LOSS/PARTIAL frequencies COUNTED from realized spreads,
             keyed on the name's own (ticker, dollar-width) history,
             pooled (DTE, delta, width) only as fallback
    window = 52 weekly expiries, trailing and causal

Results (delta-20, mid basis, 0.80x fill, top-5/day, thr 0.05):

| | in-sample 2020-25 | 2026 OOT |
|---|---|---|
| qty1 final | $77,417 (+674%) | $16,254 (+62.5%) |
| Sharpe (wk) | +3.69 | +5.82 |
| MaxDD | -5.8% | -2.6% |
| trades | 1,785 | 166 |

Prior canon (`rv_vs_iv`, k=10, 210d) was $68,891 / Sh 3.53 / DD -2.6% in-sample.

### Why rv_vs_iv was wrong at 20-delta

It compared two Black-Scholes laws (RV-vol vs IV-vol) — same N(d2) both sides,
so the divergence only re-expressed vol level. Measured consequences:

- The RV-implied triple **saturated to (1,0,0) on 51% of candidates**. 10-day RV
  fed into a 1-4 DTE BS says the strike is unreachable. That is a numerical
  artifact, not a belief.
- **DKL correlated POSITIVELY with P&L (+0.142)**, so `exp(-k*DKL)` was
  penalising the VRP edge, not risk. IV exceeds RV ~96% of the time, so a large
  unsigned divergence nearly always means "overpaid", not "dangerous".
- The k-sweep fell monotonically in return; k>0 only ever bought drawdown.

### What the fix actually changed

`ro` was previously derived as `P(short ITM) - P(long ITM)` from a single-leg
table with 0.1-wide delta buckets. At 20-delta the legs sit a median **0.089**
delta apart — inside one bucket — so both legs returned the same p_itm and
**ro collapsed to exactly 0 on 19.5% of candidates**; a further 7.8% of rows
have a BACKWARDS delta gap (vendor artifact) that the clamp also sent to 0.
Counting outcomes directly drops ro=0 to 4.9%, near the true pin rate.

### Design decisions, each tested not assumed

- **Long leg is NOT delta-matched.** It is whatever the ladder gives
  ($0.50/$1/$2.50/$5). A spread is specified by short leg + width.
- **Width bucketed by DOLLAR ladder, not % of spot.** Dollar won on Sharpe
  (3.78 vs 3.63). Sigma-normalised (width / expected move) was worst — the
  theoretically cleanest option lost.
- **ticker-first, pool as backstop** (user direction): pooling for everyone
  makes each name's estimate drift with the pool's composition rather than with
  the name. Pool is used only where a ticker lacks data.
- IV is NOT in the key. Spread-level samples are ~40x scarcer than leg-level.
- Pure per-ticker CONVERGES to pooled as N grows ($72k@N=8 -> $81.8k@N=100) and
  never beats it, i.e. no standalone name effect in breach behaviour.

### KNOWN TENSION — read before extending

On **2026 OOT, pooled beat ticker_w on every metric**: $16,654 / Sh 6.64 /
DD -1.6% (k=8) against ticker_w's $16,254 / 5.82 / -2.6% (k=10). ticker_w was
selected on in-sample drawdown, and that edge did not survive. A k=0 control
gives Sh 5.11 / DD -4.1%, so **the DKL penalty itself validates out of sample**
— it is the ticker-vs-pool choice that OOT contradicts, not the construction.
ticker_w is canon by explicit user preference (estimates should not move with
pool composition). Revisit if live behaviour disappoints.

Over 200 cells were searched on one in-sample window (delta, credit gate, k, T,
window type, N, bucket width, width definition, pooled/ticker/blend) and the
whole band was Sharpe ~3.4-3.9. Treat single-cell margins as noise.

### Files

- `spread_triple.py` (NEW) — the canonical lookup. `install_window(asof)` then
  `lookup(ticker, dte, short_delta, width)`.
- `build_spread_outcome_table.py` (NEW) — builds `output/spread_outcomes.parquet`
  (127,666 realized spreads, 300 expiries; WIN 83.9 / LOSS 8.37 / PARTIAL 7.73).
- `report_52_10_canon.py` (NEW) — regenerates both webapp payloads.
- `ground.py` — `DKL_REFERENCE = "empirical_vs_iv"`, new branch calling
  `spread_triple`. `DKL_K = 10`.
- `historical_probs.py` — added `DELTA_BUCKET_MULT` (default 10, unused by canon).
- `live/templates/backtest.html`, `oot.html` — added `DKL` and `window` chips.

### NOT DONE — live path still on the old window

`empirical_runner.TRAIL_DAYS` is still **210 calendar days** and
`live/ranker.py:44` still calls `install_latest_cached()`. The live scan has NOT
been switched to `spread_triple`. Do that, with a cache key that includes the
window spec, before relying on live scores. `output/empirical_window_cache.pkl`
is keyed only on pool mtime + date, so a semantics change would otherwise serve
stale tables silently. Nothing was deployed to Mya in this session.

---

## 0.19 IV skew as a directional feature (2026-09-11) — **OPEN RESEARCH TASK**

**This is the open research item.** Measured, significant on one year, NOT
validated out of sample. Nothing in canon uses it. Do not enable it live.

### Why

GEPO has no directional signal. `GROUND = g − k·DKL` is a volatility score, so
whether a day's book ends up bullish or bearish is a side effect of which
candidate ranked higher. The euro lane showed what that costs: the same SPXW
`bear_call` rule won 58% in 2025 and 34% in 2024, and pooled to exactly 50%
over three years — a 50-delta short with no directional information in it.

### The feature

25-delta risk reversal, per (Symbol, DataDate):

    skew = median IV(puts  |delta| 0.20-0.30)
         − median IV(calls |delta| 0.20-0.30)

Positive = puts richer than calls = the market is paying for downside.
A **band** median, not the single nearest strike — matching one strike lets
vendor IV outliers through and produced skews of ±9 before filtering.

Computable from data already fetched: every scan pulls both rights across the
chain, and `master_pool.parquet` carries `ImpliedVolatility`, `abs_delta` and
`putcall_norm`, so this is available back to 2020 with no new download.

### Result on 2026 OOT — the only test run so far

Built every candidate the OOT scorer would see (17,736), realized them, joined
skew. **Deliberately unconditioned**: no GROUND scoring, no top-5 selection, so
the sample is not already filtered by the signal skew is meant to add to.
13,399 realized and skew-matched (76% coverage).

Quintiled by skew, Q5 (high) minus Q1 (low):

| | P&L/contract | t | breach rate | t |
|---|---|---|---|---|
| bull_put  | **−10.47** | **−2.64** | +2.87pp (48.6→51.5) | +1.43 |
| bear_call | **+9.05**  | **+2.53** | **−4.54pp** (53.3→48.8) | **−2.44** |

Both P&L gradients significant, and **they point opposite ways**, which is what
they must do if skew carries directional information. Read as one story: high
skew predicts a DOWN move, so short puts get breached more and short calls
less. Two samples, opposite signs, same underlying claim — harder to get by
chance than either gradient alone.

### What is NOT established

- **One year only.** The euro lane looked exactly this convincing on 2025
  (+114%) and collapsed to a 50% win rate once 2024 and 2023 were added. Treat
  2026-only significance as a hypothesis.
- **No incremental lift measured.** Every skew bucket has NEGATIVE mean P&L
  (−7 to −22) because this is the whole candidate universe, not GROUND's
  selection. The open question is whether skew adds anything ON TOP of GROUND
  or merely re-expresses what GROUND already extracts from IV. A hint on the
  182 GROUND-selected picks that matched a skew: `bear_call` win rate ran
  67% / 56% / 54% across skew terciles — n=39 per bucket, suggestive only.

### Next steps, in this order

1. **Recompute skew from `master_pool.parquet` for 2020-2025** and re-run the
   quintile test. If the gradient does not survive, stop here.
2. **Measure incremental lift**, not raw predictive power: score candidates
   with GROUND, then bucket the QUALIFYING picks by skew. That is the only test
   that answers "does this improve GEPO".
3. Only then consider wiring it in — as a sixth bucket dimension on the
   empirical pool (`skew_rank_bucket`, ranked per ticker against its own
   trailing history exactly like `iv_rank_bucket`), so `p` becomes
   `P(breach | delta, IV, DTE, iv_rank, skew_rank)`.

### Do not

- Do NOT build a separate directional model and multiply its probability into
  the ranking. GEPO's `p, q, ro` from `historical_probs.empirical_lookup_probs`
  ALREADY estimate `P(short leg breached)`. Multiplying two probabilities each
  fitted on the same outcome double-counts and will flatter in sample.
- Do NOT skip the IV hygiene. The vendor IV column is badly contaminated: at
  DTE 3-7 the 99th percentile is 4.22 and the max is 51.0. Unfiltered, the
  extremes are nonsense (KHC puts at 180% vol against 26% calls). The euro lane
  filters non-positive IV in `build_euro_pool.py`; **the SP100 production pool
  does not**, and the equity data is worse — 14.1% of rows have IV <= 0 against
  the euro lane's 12.8%.
- Do NOT expect a sixth bucket dimension to be free.
  `empirical_runner.build_window_tables` already raises
  `ValueError: Bin edges must be unique` when a dimension degenerates; more
  dimensions thin every cell. Expect to need fewer skew buckets (3, not 5).

### Historical adjacent finding — `DELTA_TARGET` had not been swept

`config.DELTA_TARGET = 0.50` is inherited from the source paper ("closest to
but not exceeding 0.50") and there is **no delta sweep among the ~40 backtest
scripts in the repo**. At 0.50 every short leg is at the money: maximum
premium, maximum pin exposure, and a structural ~50% win rate. This is a
first-order untested parameter and arguably a larger lever than any directional
feature, because it changes pin risk, win rate and the direction problem at
once. This was swept on 2026-09-11; §0.20 supersedes this note and makes
0.20 / 0.10-0.30 the current canon.

---

## 0.18 Assignment monitor + Actuals rebuild (2026-09-11) — CURRENT STATE

Live-ops session. No canon or strategy change. All of it is detection and
display; `live/assignment_risk.py` never places or closes an order.

### Assignment monitoring — the rule now

ONE warning: **pin risk**, rendered yellow on the Actuals tab.

    pin = short leg ITM AND long leg OTM, i.e. spot INSIDE the strikes

That is the only configuration that delivers real shares — the short is
assigned and the long expires worthless. Both legs ITM is NOT this case: they
exercise against each other and settle to cash.

Fixes behind that one line, each of which was a live false positive or miss:

- **The pin test is INCLUSIVE of both strikes** (`lo <= spot <= hi`). A strict
  test called KO safe on 2026-09-11 with spot exactly 88.00 against an 87/88
  zone. The long-strike end is the expensive one: settling exactly AT the long
  strike leaves the short assigned while the long sits ATM, misses the $0.01
  auto-exercise threshold and expires worthless — a full delivery the strict
  test reported as clear.
- **The legacy expiry-day flag now requires the long leg OTM too.** It tested
  "expiry day AND short leg ITM", was dormant every other day, and on
  2026-09-04 lit up SBUX, AMGN and DE — all 0.2 to 43 points THROUGH their long
  strike and settling cleanly to cash — while HD, the only genuinely pinned
  position, was one of several flagged identically.
- **A past dividend is not a benefit.** `_exercise_benefit` tested only
  `dt <= exp` and never checked the ex-date was still ahead, so GM on
  2026-09-11 reported 0.18 from an ex-date of 2026-09-04 and tripped channel 1
  on a benefit nobody could capture. Now requires `today <= ex_date <= expiry`.
- **Extrinsic fails CLOSED when unquotable.** `extrinsic` comes from the parity
  leg, which is deep OTM exactly when the short leg is deep ITM — so it stops
  quoting precisely when the position is most exposed. Requiring
  `extrinsic is not None` cleared the flag on DE at 49.93 ITM, $69,861
  notional, with no warning at all.

### Removed deliberately

- **Channel 2 ("bid < intrinsic")** is gone. It is algebraically identical to
  `extrinsic < half the bid-ask spread` — verified 6/6 against the live book —
  so its threshold was never a chosen constant, it floated with market width.
  DE got a 3.45 threshold and PEP 0.17. It fired on PEP whose own mid (0.83)
  sat BELOW its own intrinsic (1.00), an impossible quote, and missed DIS at
  0.08 extrinsic with a tight book.
- **Extrinsic collapse no longer warns.** It fired on every spread that had run
  deep ITM — which the Ext column and the P&L column already say — and on a
  book closed every Friday afternoon it never became an action. The value is
  still computed and still displayed.

### Telegram alerts

`notify_watcher.sh` on Mya de-dups on FILENAME (`grep -qxF "$fname" .processed`),
so the old one-file-per-day payload notified at most once per day: whatever the
09:30 scan happened to say, almost always "no assignment risk". Split in two:

- `assignment_risk_<date>.json` — state for the webapp, rewritten in place,
  deliberately carries **no `message` key** so the watcher skips it silently.
- `assignment_alert_<date>_<HHMMSS>.json` — one per firing, uniquely named,
  `message` present. A new filename is never in `.processed`, so it always
  sends.

Fires **once per option per expiry**. The ledger
(`.assignment_alerted.json`) is keyed `ticker|spread_type|short_strike|expiry`
and pruned by expiry, so it self-cleans weekly. Message is a header plus one
line per option.

### Actuals tab

- **Row P&L is now the number the card header sums.** They used different
  sources: the header priced against `actual_credit` (your recorded fill), the
  rows against the MODELLED 0.80×mid entry. On 2026-09-08 the Sep 11 card read
  −49 while its rows showed −11/+2/+24, and ABT and ISRG rendered as WINNERS
  when both were losses. The error always flattered — a worse-than-modelled
  fill was invisible.
- **Spot is the freshly-quoted price**, not the last scan's. The scan fetches a
  strike band around current spot, so a position drops out of tracking exactly
  when it moves deep ITM. `assignment_risk` quotes each position's own
  underlying every run. The /actuals route overwrites `last_track` and
  recomputes `live_status` from it, so Spot, Status and the week totals cannot
  drift apart.
- **Ext column** added beside Mark. The two fail in opposite directions: Mark
  goes blank when a position moves deep ITM (DE had `current_mark: None`),
  which is when Ext is most informative. `n/a` in red means the parity leg is
  not quoting.
- **The page no longer goes blank at midnight.** `_assignment_lookup` read
  strictly today's payload, so from 00:00 until the first scan the page lost
  every colour AND fell back to a stale spot. Now falls back to the most recent
  payload within `ASSIGN_MAX_AGE_H = 18`.
- **`_actuals_rows` MERGES the source pick** instead of replacing it.
  `pick = _json_clone(fresh)` discarded fields the narrower source lacked.
- History tab: assignment highlight removed entirely. Live risk belongs on
  Actuals, the only tab that tracks holdings.

### Snapshot picks lost their deltas — fixed and backfilled

`snapshot_picks.PICK_FIELDS` was a 14-field allowlist, so a pick copied into
Actuals from the Snapshots tab arrived with **no `short_delta` at all** — the
key absent, not null. All 8 positions open on 2026-09-04 came in that way and
the column rendered "—" permanently.

- Forward: `PICK_FIELDS` gains `short_delta`, `long_delta`, `IV`, `long_IV`,
  `short_bid/ask`, `long_bid/ask`, `short_oi`, `long_oi`. Deliberately NOT the
  `combo_*` book or the probability internals — `intraday_picks/` holds every
  scan of every day.
- Backfill: `live/backfill_actuals_fields.py`. The values survive in
  `live/ranked/<date>_<hhmm>.json`, and each Actuals trade records the source
  date and hhmm. Searches exact-scan first, then same-day NEAREST-scan — delta
  drifts intraday, and ordering by proximity moved HD from 0.4453 to the
  correct 0.4738. Dry run by default, `--apply` to write, backs up
  `actuals.json` first. 10/10 resolved.

### Quote exchange

`live_config.LIVE_QUOTE_EXCHANGE` added and set to **"ISE"** at user direction.
Quotes only; execution is unaffected and still SMART.

Measured live 2026-09-08: CBOE, PHLX and AMEX return quotes byte-identical to
SMART (no-ops). ISE differed on 2 of 4 deep-ITM contracts and was WIDER both
times — KO 100P 11.25/12.80 against SMART's 11.45/12.60. Coverage is fine: 53
of 54 real chain strikes qualify on ISE, and the one miss fails on SMART too.
Anything ISE cannot qualify is retried on SMART and logged, so a venue gap
costs a wider quote rather than losing the contract from the scan.

### Euro lane additions (2026-09-03/04)

- `build_euro_rv_table.py` (NEW) — derives the euro RV table from the
  `UnderlyingPrice` retained in the euro parquets, reusing
  `rv_table.compute_rv_table`. Reproduces production to 9.9e-16 on SPXW.
  Without it SPX/NDX/RUT score nothing, since `output/rv_table.parquet` only
  ever covered `SP100_TICKERS`.
- `config.EURO_BAD_SPOT_DAYS` + `drop_bad_spot_days()` — purely additive.
  `Greek_20250924_OData2.csv` stamps RUT and RUTW with the SPX spot
  (6637.97 vs SPX's 6637.9702) on a day the Russell traded near 2,400,
  inflating RUT RV from 0.237 to 0.785. Filtered on read; raw CSVs and year
  parquets untouched. 2023 and 2024 scanned and clean — this is the only bad
  day in three years.
- **`output/rv_table.parquet` still carries the same bad RUTW values**, and
  RUTW is in `SP100_TICKERS`, so live scans score it on corrupted RV.
  Deliberately untouched — production data, separate change. STILL OPEN.
- `build_euro_pool.py` / `report_euro_backtest.py` gained `--dte-min/--dte-max`,
  `--min-oi`, `--max-max-loss`, `--entry-dows`, `--rv-table`.
  **`historical_probs` matches DTE exactly**, so a pool built at 1-4 silently
  drops every DTE 5+ candidate at the GROUND stage rather than erroring.
- 2023, 2024 and 2025 euro parquets are built. Tuning universe/OI/entry
  days/DTE against 2025 alone reached +114%; adding 2024 and 2023 took SPXW to
  a 50% win rate over 157 trades, and at MID pricing the three-year book is
  −$7,534 because credits book +$0.683/share above mid under
  `CREDIT_BASIS="last_clamped"`. **The fill basis, not the selection, carried
  the apparent edge.**

---

---

## 0.36 Mac mini readied for the D_ent canon (2026-09-13) — CURRENT STATE

Pulled `d274db7`. `build_daily_closes.py` run once, as §0.35 requires.

### daily_closes coverage — the seeder only found ONE source file

**Amended same day:** `477f2d4` dropped `ent_canon.MIN_OBS` from 120 to 1, so a
name is no longer unscored for thin history — P_real now uses whatever it has,
leaning on the 0.5 pseudo-count. That removes the hard blocker described below.
The backfill is still worth having: P_real over 53 sessions is a far noisier
estimate than over 252 (`WINDOW = 252`), and the verification finding about
adjusted closes stands regardless.

`build_daily_closes.py` globs `output/20??_sp500_last.parquet` plus the 2026 OOT
combined file. **The 2020-2025 year parquets are not on the Mac mini** (§0.16),
so the glob matched nothing and the store seeded from 2026 alone: 14,356 rows,
98 tickers, 2026-01-01 -> 2026-08-18. `live.closes.load_closes()` unions the
store with the live snapshots, which carries it to 2026-09-11.

That left **13 equity tickers under the 120 sessions `P_real` then required** — ten at
exactly 53 sessions (AON APD BDX CB CCI DUK ITW LIN SYK WM ZTS), NSC at 98, MMC
at 4. On a Thursday snapshot that cost 25 of 63 candidates, dropped as unscored.

Filled from the Yahoo chart API (`indicators.quote[0].close`, the RAW close, not
`adjclose`), one year per ticker, **verified against the vendor before writing**:
11 of 12 matched the vendor EXACTLY (median and max diff 0.0000% across 655
overlapping rows).

- **BDX was the exception** and is why the verification mattered: a constant
  21.3836% gap on 2026-01-08..01-16, ratio 0.7862, then exact from 2026-02-12.
  That is a corporate action Yahoo back-adjusted and the vendor did not. An
  adjusted close is the WRONG series for `P_real`, which measures realized
  exceedance of strikes quoted in unadjusted terms. Only BDX rows from
  2026-02-12 were appended.
- 2,347 rows added. Every equity ticker that can trade now clears 120 sessions.

**Always verify a price backfill against the vendor on overlapping dates before
appending.** `data/spy_us_d.csv` has the same disease in the opposite place —
its `Close` is dividend-adjusted while its OHLC is raw, so 87.6% of 2025 rows
have `Close` outside `[Low, High]`.

### MMC is a dead symbol, not a data gap

MMC fails qualification on Yahoo (404) AND on IBKR ("No security definition has
been found"), appears in **0 of 205** September snapshots, and logs
`[MMC] no spot price` on every scan. Its 4 store rows are January vendor data.
It can never produce a candidate and burns a fetcher slot every run. Worth
removing from `SP100_TICKERS`; harmless otherwise.

### Verified the ranker runs under D_ent on this machine

Thursday snapshot 2026-09-10/1545: smile fit on 76 chains, 63 candidates -> 32
ranked, 0 above the 0.01 threshold on that particular snapshot.

**Two traps when testing offline:** the IBKR combo book is dead outside market
hours, so every candidate fails `credit>0` and the run looks broken — set
`live_config.LIVE_COMBO_ENABLED = False` to isolate. And Friday snapshots are
DTE 0 (`LIVE_DTE_MIN=0`), where delta is a step function (25th pct 0.922, median
0.996), so no delta band finds anything. Fridays produced 6-7 picks under every
canon tried; that is structural, not a regression. Test on Mon-Thu snapshots.

### Ranker no longer crashes when nothing ranks

`_serialize` did `ranked["GROUND"] > 0` without the empty guard the line above
already used. An empty frame has no columns, so it raised `KeyError: 'GROUND'`
on the exact path that prints "nothing ranked; writing empty payload anyway".
`latest.json` was never written and the live page froze on the last good scan —
observed 2026-09-11, stale from 14:33 to 15:23 while three scans appeared to
succeed. Latent for a long time, harmless while some candidate always ranked.

---

---

## 0.37 Credit targets made model-relative, and what the fill is worth (2026-09-13) — CURRENT STATE

Nine commits after `4fcd344`. The substantive one is `5da1aa4`; the rest are the
web app and one research script.

### The min is a multiple of the spread's own model credit (`5da1aa4`, floor set by `87901c2`)

`9f251e0`, earlier the same day, replaced the relative multipliers with ABSOLUTE
credit/width levels (`MIN_CW, TARGET_LO_CW, TARGET_HI_CW = 0.50, 0.53, 0.56`).
That was the wrong fix to a real bug. The bug was cosmetic: at `fair_cw` 0.467,
1.04x = 0.4853 and 1.06x = 0.4946, and both print 0.49 at 2dp, so min and
target_lo rendered identical.

A flat level cannot work, because **credit/width is not a constant at a given
delta — it depends on the spread's width in units of sigma**. Measured on 76
SP100 names at delta 0.50 / DTE 5 with each name's real spot and ATM IV:

| width | median w/sigma | credit/width (median) |
|---|---|---|
| 0.5 | 0.06 | 0.472 |
| 1.0 | 0.12 | 0.460 |
| 2.5 | 0.30 | 0.425 |

The ceiling is `N(d2)` = 0.487 and only a zero-width spread reaches it. For cheap
underlyings a 2.5 width is 3-4 sigma and c/w collapses: F 0.096, PFE 0.129, T 0.131.
So the flat 0.50 was 32% ABOVE fair on HON (model 0.95 on a 2.5 width, min demanded
1.25 — unreachable) and BELOW fair on GS (model 1.41).

Now `MULT_MIN, MULT_TARGET_LO, MULT_TARGET_HI = 1.04, 1.06, 1.10` plus
`MULT_WALKAWAY = 1.00`, all applied to the spread's own `model_credit`. Ratios
carry 3dp so two levels can never round together again.

`5da1aa4` first set the floor at 1.00x (fair value). `87901c2` raised it back to
1.04x the same day, because break-even is ~1.03x once the $1.30 commission is
paid — at exactly fair value the trade is a coin flip that pays the broker. 1.00x
is retained as `MULT_WALKAWAY`, the walk-away line. The execution gate now
requires the IBKR credit >= 1.04x model. §0.38 shows why this matters: live
credit/width is running 0.503 against the backtest's 0.543, and every point of
credit is worth ~$5,800 of IS P&L.

**`fair_cw(delta, dte)` has no width term** (`ent_canon.py:261`) and therefore
returns 0.433 for any delta-0.50/DTE-5 spread, where theory spans 0.46 to 0.24.
It overstates worst on wide, low-IV names (BMY 0.537 vs BS 0.476, +13%). This only
bites on the `basis: 'formula'` fallback path when no smile fit exists. Not fixed.

### What the fill is actually worth — repriced exactly on both books

Both published books repriced at multiples of model credit, holding selection and
qty=2 fixed (GROUND scores off `model_credit`, not the fill, so the trade set does
not move). Reproduction at the published 1.08x is exact to $0.000000 on all 3,997
IS and 544 OOT trades.

| fill | IS total P&L | IS return | OOT total | OOT return |
|---|---|---|---|---|
| 1.10x target hi | $68,288 | +682.9% | $10,571 | +105.7% |
| **1.08x published** | **$56,694** | **+566.9%** | **$8,892** | **+88.9%** |
| 1.06x target lo | $45,097 | +451.0% | $7,214 | +72.1% |
| **1.00x = min** | **$10,248** | **+102.5%** | **$2,170** | **+21.7%** |
| 0.965x breakeven | -$10,121 | -101.2% | -$777 | -7.8% |

About **$5,800 of IS P&L per 1% of model credit**. At exactly model credit you keep
18% of the backtested P&L and sit 3.5% of credit from wiping the account. The
0.965x row landing at -$121 final is an independent confirmation that
`MULT_BREAKEVEN = 0.965` is correctly calibrated. Win/partial/loss rates are
IDENTICAL across every row (44.2/15.3/40.5) — outcome depends only on where spot
landed, so the entire difference is fill quality.

**Fair value is a floor, not a target.** Essentially all of the return lives in the
1.06-1.10x band. This table is the argument `87901c2` acted on: at 1.00x you keep
18% of the book and sit 3.5% of credit from zero, so the gate was put at 1.04x and
1.00x kept only as the walk-away line.

### Validated against the 19 real fills

`live/actuals.json` stores no `model_credit` (all 19 are pre-canon snapshots), so
model credit was reconstructed by refitting `ec.fit_smiles` on the stored chain
nearest each fill time and repricing the exact strikes. 18 of 19 recovered; NEE's
chain had no valid fit.

**fill/model: median 1.111, mean 1.165, range 0.953-1.795.**

- `FILL_MULT = 1.08` holds up — the backtest assumption is marginally CONSERVATIVE
  against real fills, not optimistic.
- **4 of 18 filled below model** (MA 0.953, CSX 0.974, GS 0.978, ISRG 0.996), so even
  fair value is a live gate that rejects about one trade in five, not a formality.
- Clearance against each candidate floor: **1.00x 14/18 (78%), 1.04x (the gate as
  shipped) 13/18 (72%), 1.06x 11/18 (61%), 1.10x 9/18 (50%)**. So the 1.04x floor
  costs one more trade than fair value and buys the commission back.

Caveats: refit is up to ~2 min off the fill time; the mean is dragged by PEP 1.795
and MDT 1.536, both cheap spreads where a ~$0.28 model credit inflates the ratio
(PEP's looks like a poor fit on a thin chain); and these are trades that were
CHOSEN, partly because the quote looked rich, so the distribution across all ranked
picks sits lower.

### Partials, and a caption that misstates the code

A PARTIAL is spot finishing between the strikes. `report_ent_canon.py:29-36`:
`pnl = c - intrinsic`, then **halved only if positive**. Losing partials take the
full loss. Asymmetric by design.

| | IS 2020-25 | OOT 2026 |
|---|---|---|
| partials | 611 / 3,997 (15.3%) | 101 / 544 (18.6%) |
| paid (halved) | 353 -> +$13,056 | 49 -> +$2,391 |
| lost (full) | 258 -> -$17,849 | 52 -> -$4,233 |
| **net** | **-$4,792** (-8.5% of book) | **-$1,841** (-20.7%) |

Partials are a net DRAG in both books, not a soft middle outcome. A paying partial
returns a median $12.64/contract; a losing one costs a median -$23.05 and reaches
-$130.43. The pay/lose line sits at `credit/width` = 0.531 median, i.e. the first
~53% of the pin zone measured from the short strike pays. P&L slides monotonically
across the zone, +$8,155 in the first fifth to -$10,908 in the last.

**BUG (not fixed): the caption "partial-WIN at 50% intrinsic" misstates the code.**
It halves the NET P&L, `0.5*(c - intrinsic)`, not the intrinsic. On c=1.00,
intrinsic=0.40 the label implies $0.80 and the code pays $0.30. Same wording, same
`pnl *= 0.5`, in all four implementations — `report_ent_canon.py:35`,
`report_mid_canon.py:46`, `backtest_midsel_sweep.py:119`,
`report_three_sizings.py:176`. The code is uniform and more conservative than the
label, so published curves are pessimistic here, not optimistic. Only the wording
is wrong. $13,056 of surrendered haircut is ~23% of IS book P&L, so the assumption
does real work and should be described accurately.

### There is no hit-rate edge — the entire edge is price

| | IS | OOT |
|---|---|---|
| mean \|short delta\| | 0.549 | 0.546 |
| delta implies P(OTM) | 45.1% | 45.4% |
| **actual per-trade WIN** | **44.2%** | **42.8%** |
| money-positive trades | 53.0% | 51.8% |
| weekly win rate | 58.8% | 64.5% |

The per-trade rate is exactly what delta predicts, marginally below it. The
58.8%/64.5% weekly figures are **aggregation, not skill** — ~15-18 trades a week
with slightly positive expectancy makes most weeks green. Bundling by month would
push it toward 100%. Do not quote the weekly number as evidence the strategy beats
its delta.

Weekly detail: IS 161/274 weeks up, median week +$234, up week +$934 vs down week
-$830, profit factor 1.60. OOT 20/31, median +$308, profit factor 2.49 on 31 weeks.
By year: 2020 61.9%/+$8.6k, 2021 62.0%/+$12.1k, 2022 56.9%/+$7.4k, **2023 49.0%/+$3**,
2024 65.4%/+$20.9k, 2025 59.2%/+$7.7k, 2026 OOT 64.5%/+$8.9k. **2023 is the
worst-case regime the strategy has seen and it looks like flatness, not drawdown.**

### D_ent risk evidence — suggestive in-sample, NOT confirmed, ablation not yet run

Calmar (CAGR / max drawdown): IS 1.85 (CAGR 41.7%, maxDD 22.6%, Sharpe wk 1.34).
OOT 14.27 but that annualizes 0.63 years — not a real rate.

Quartiling the published IS book by D_ent:

| quartile | median D_ent | P&L | maxDD | Calmar |
|---|---|---|---|---|
| Q1 low | 0.041 | $15,519 | 10.5% | 1.80 |
| Q2 | 0.085 | $14,755 | 10.7% | 1.70 |
| Q3 | 0.136 | $12,361 | 21.6% | 0.74 |
| Q4 high | 0.214 | $14,058 | 33.0% | 0.53 |

P&L flat while **max drawdown triples and Calmar falls monotonically** — exactly the
claimed shape. **OOT does NOT reproduce it**: Q1 4.13, Q2 0.84, Q3 0.59, then Q4
inverts to 9.70 with the LOWEST drawdown (7.6%). On 136 trades over seven months
that is plausibly noise, but it is not confirmation.

**This is not an ablation.** Every trade in both books already passed selection at
k=1.0, so it compares survivors. `research/dkl_2026_09_13/ablate_k.py` (`021dac4`)
reruns `select()` at k in (0, 0.25, 0.5, 0.75, 1.0, 1.5, 2.0) — k=0 is the penalty
off, GROUND collapses to EV — on the same frame, threshold, top-N and fill, and
reports Calmar.

**It cannot run on the Mac mini.** It needs `research/dkl_2026_09_13/featATM6.parquet`
(gitignored, ~1.4 GB). The whole intermediate chain is absent here —
`/tmp/gepo_pairs.parquet`, `is_synth.parquet`, `oot_pairs_q.parquet` — the mini
carries only 2023-2024 vendor data, and `output/master_pool.parquet` has no strikes
or prices. **Run it on the MacBook.** The script exits with that message rather than
failing obscurely.

### D_ent is unsided, and its reference is the uniform, not P_real

`d_ent` is `ln3 - H(Q)`, pure entropy, so it is invariant to permuting the states:
Q=[0.55,0.20,0.25], [0.20,0.55,0.25] and [0.25,0.20,0.55] all give 0.101341. It
cannot distinguish a 55% WIN from a 55% LOSS.

More important: it measures divergence from **U_3, the uniform**, and never sees
`P_real`. `market_triple()` builds Q from BS `N(d2)` at fitted leg IVs and that is
the only input. So `exp(-k*D_ent)` penalises **certainty, not disagreement** — a
confident market and a mispriced market look identical to it. Nothing in current
scoring can express "this one leans the wrong way", which is the gap §0.19 (IV skew)
was meant to fill.

`DKL_REFERENCE` is quoted in START HERE as `"entropy_uniform"` but **no such
constant exists in `ent_canon.py`**. The behaviour is as described; the name is not
in the code.

Direction split (side effect of strike placement, not of D_ent): IS bull_put 3,153
trades +$52,115 / bear_call 844 +$4,579. OOT bull_put 464 +$9,727 / bear_call 80
**-$835 on a 32.5% hit rate**. The book is 79% bull_put and that is where the money is.

### Web app (`35af0ef`, `6aae5f7`, `4d41719`, `8577d5e`, `03eae52`, `1a45398`)

- Fill-sensitivity table (0.90x-1.10x model) in a collapsed chip on Backtest and OOT,
  shared partial `live/templates/_fill_sensitivity.html`.
- GROUND % one format everywhere, and 1dp on Actuals.
- Table columns sized by content, headers wrap (`th { white-space: normal }`); the
  880px `min-width` on `.table-scroll table` is gone.
- Live tab: Top 5 cards moved BELOW the ranked table; the 16 canon chips fold behind
  one `params` chip (`details.params-fold`). Vol-gate banner and weekly totals stay
  at top — a gate warning below the fold is useless.
- Fill-grade tags ("fill below-min / ok / target") removed from Actuals and History.
  `webapp._attach_targets` still computes `pick["fill_grade"]`; nothing reads it, so
  re-enabling is a template-only change.
- Actuals width: dates lose the year ("Sep 8"), strikes lose trailing zeros but keep
  a real half ($205, $202.5, $202.25). Two new Jinja filters, `shortdate` and
  `strike`, beside the existing `rd`.

All deployed to BOTH Mya checkouts (`/opt/vito/gepo-backtest`,
`/opt/vito/gepo-euro-backtest`) with `pm2 restart` and verified by `curl` against
the live URL, per [[feedback-verify-the-artifact-not-the-function]].

### Monday readiness (verified 2026-09-13, not assumed)

- `origin/main` clean, nothing unpushed.
- `output/daily_closes.parquet`: 16,703 rows, 98 tickers, 2025-09-02 -> 2026-09-11.
  `WINDOW = 252` is satisfied; the §0.36 `build_daily_closes.py` prerequisite is done.
- Canon in force: `K=1.0`, `THR=0.01`, `TOP_N=5`, `FILL_MULT=1.08`, `COMMISSION=1.30`,
  `MULT_MIN/LO/HI = 1.00/1.06/1.10`, `WINDOW=252`, `MIN_OBS=1`;
  `DELTA_TARGET=0.55` band 0.50-0.60, `GROUND_THRESHOLD=0.01`.
- Live: `LIVE_QUOTE_EXCHANGE=ISE`, `LIVE_SELECTION_CREDIT=quoted`,
  `LIVE_MIN_OPEN_INTEREST=0`, `LIVE_DTE_MIN/MAX=0/6`, `LIVE_COMBO_MAX_WIDTH=1.0`.
- Crontab installed and unchanged (scans :00/:15/:30/:45 9-15 Mon-Fri, 16:00 close,
  daily bars 17:01, health 5/20/35/50). Not modified — see
  [[feedback-dont-touch-live-crontab]].

**Note:** `config.MIN_CREDIT_RATIO` is now `0.0` (was 0.30). Changed on the MacBook,
not by this session; flagged here only so it is not mistaken for drift.

### Still open

1. **Run `ablate_k.py` on the MacBook** — the only real test of whether D_ent earns
   its place. Everything else about it is correlational.
2. **§0.19 IV skew** — measured on 2026 OOT, NOT validated on 2020-2025, no
   incremental-lift-over-GROUND test. Still the open research task.
3. **"50% intrinsic" caption** wrong in four scripts (above).
4. `fair_cw` has no width term (above).
5. **MMC is a dead symbol** and should leave `SP100_TICKERS` (§0.36).
6. `output/rv_table.parquet` still carries corrupted RUTW values while RUTW is in
   `SP100_TICKERS`.
7. `report_oot_2026.py` books `last_clamped` while live uses mid.

## 0.38 IBKR-only replay of the D_ent canon (2026-09-13) — CURRENT STATE, ACTION NEEDED ON THE MACBOOK

Replayed the current canon over the days we hold IBKR chains for and compared it
with what the canon of the day actually picked. **The new canon underperformed.**
The window is only 12 days because of a data gap described below.

### The result — 2026-08-20 .. 2026-09-10, 12 days

| book | n | med c/w | win% | avg WIN | avg LOSS | payoff | total |
|---|---|---|---|---|---|---|---|
| **NEW canon @1.08x model** | 56 | **0.503** | 42.9% | +$88.72 | **-$90.29** | **0.983** | **-$21** |
| OLD canon (as booked) | 54 | — | 53.7% | +$103.73 | -$81.16 | 1.278 | **+$1,350** |

Booked on identical bases so the fill is not doing the work — at 0.80x/0.90x/1.00x
each pick's own leg mid the new canon runs -$120 / +$472 / +$1,064 against the old
canon's +$1,351 / +$1,926 / +$2,501. The new canon's 1.08x model is in fact the
RICHER assumption in aggregate ($51.15 vs $49.96 per share across all picks).

**The cause is delta, and the mechanism is credit/width.**

| book | n | med c/w | win% | payoff | break-even win% |
|---|---|---|---|---|---|
| backtest 2020-25 | 3,997 | 0.543 | 44.2% | 1.171 | 46.1% |
| OOT 2026 | 544 | 0.546 | 42.8% | 1.224 | 45.0% |
| **live replay (12d)** | 56 | **0.503** | 42.9% | **0.983** | **50.4%** |

The win rate is the same in all three (43-44%, exactly what delta 0.55 predicts —
see §0.37, there is no hit-rate edge). What differs is credit per unit width: at
0.543 a loss costs 0.457 of width and a 44% hit rate clears; at 0.503 the loss
costs 0.497, the payoff collapses to 0.98, and break-even rises to 50.4%. **The
average win barely moved ($80 -> $89); the average loss blew out ($68 -> $90.)**

The old canon ran a 0.35-0.65 band and its median short delta was **0.489, with 32
of 54 picks below 0.50** — further OTM than the new band allows. The two canons
agreed on only 16 of ~94 picks.

**Do not act on this yet.** 56 trades is far too few; one bad day moves the total
by more than the gap. The in-sample case rests on 3,997 trades. What it does say is
that live evidence is running AGAINST the ATM change so far, and the open question
is now specific: **why are live spreads ~4 points cheaper per unit width than the
backtest's?** Two untested candidates — width relative to sigma (§0.37 shows c/w
falls as width/sigma rises, and the replay is 32/56 at width 2.5 on mid-priced
names), and pool depth (the backtest picks 5 from 43,479 candidates over six years;
these days offered 28-55 ranked candidates each).

### Why the window is 12 days and not 15 weeks — THE BLOCKER

`live/frozen/` goes back to 2026-05-20 and the History tab renders it fine, but
**frozen files are not chains.** Each carries its five picks with their own legs
(short_bid/ask, long_bid/ask, oi, last). `ec.fit_smiles()` needs the WHOLE strike
ladder per (ticker, expiry) to produce c0/c1/c2/sig0, and only then can
`price_spreads()` give a `model_credit` for arbitrary candidate strikes. Five picks'
legs cannot make a smile, and they are the strikes the OLD canon chose anyway.

So a day is replayable only if `live/snapshots/<date>/` exists. On the Mac mini the
oldest is **2026-08-20 — the day after the cutover (§0.12, 2026-08-19).** Checked
and ruled out as sources:

- **Mya has no chains at all.** No `live/snapshots/` on either checkout; exactly two
  parquet files on the whole box (`analysis/vix_daily.parquet` x2); the entire
  `/opt/vito/gepo-backtest` tree is 21 MB. By design — `upload_to_mya.sh:186` ships
  `live/frozen/` and `live/data/`, never `live/snapshots/`.
- **Rebuilding from IBKR historical option bars is not practical** — tens of
  thousands of paced requests (60 per 10 min), and expired-option history is spotty.
- `output/2026_sp500_last_oot_combined.parquet` DOES cover 2026-01-01..08-18 and
  would fill the gap exactly, but it is vendor data, excluded by instruction.

**=> The chains for 2026-05-27 .. 2026-08-19 should be on the MACBOOK**, which was
the production runner until the cutover. That is the one place not yet checked.

### UPDATE 2026-09-13 (MacBook): chains staged on Mya for the mini

The MacBook holds `live/snapshots/` for **2026-05-19 .. 2026-08-20** (55 days, 620
parquets, 45 MB; every May-Aug frozen file's `snapshot_file` resolves). No IB Gateway
runs on the MacBook, and its `output/daily_closes.parquet` ends 2026-08-21, so the
replay was NOT run here. The chains were rsynced to Mya, outside the app tree
(nothing published): `~/gepo_transfer/snapshots/` on `$MYA_SSH_HOST`.

On the Mac mini (does not touch the mini's own 08-19/08-20 files):

```
. ~/.gepo_env
rsync -av --ignore-existing "$MYA_SSH_HOST":~/gepo_transfer/snapshots/ live/snapshots/
python research/ibkr_replay/fetch_ibkr_closes.py      # IBKR closes through today
python research/ibkr_replay/replay_canon.py --since 2026-05-27
```

### To run it on the MacBook

```
ls live/snapshots/ | head            # is May-August there?
du -sh live/snapshots

python research/ibkr_replay/fetch_ibkr_closes.py      # ~10 min, IBKR daily bars
python research/ibkr_replay/replay_canon.py --since 2026-05-27
```

`research/ibkr_replay/` (committed this session):

- **`fetch_ibkr_closes.py`** — N years of IBKR daily TRADES closes for the snapshot
  universe, into its own parquet. Deliberately does NOT touch
  `output/daily_closes.parquet`, which is vendor-seeded and Yahoo-backfilled (§0.36),
  so the two never mix. Read-only, client id 178, 6s pacing.
- **`replay_canon.py`** — reruns `rank_snapshot` per day on the exact snapshot the
  freeze used, settles against the IBKR closes, prints the table above. `--since`
  and `--fill` are parameters. Skips days with no stored chain and says how many.
  Stubs `_reprice_on_combos`, because asking IBKR for a combo quote now would price
  TODAY's book against historical strikes; leg mids from the snapshot stand in and
  are mildly conservative.
- Parquets and pickles in that directory are gitignored.

Verified on the mini: the committed scripts reproduce the 12-day numbers exactly.

### Selection x gate on the same replay (2026-09-13, late) — DEPLOYED

D_ent canon, fill 1.08x model, qty 1, 2026-05-27..09-10, IBKR chains, all four combinations:

| selection / gate | traded | days | total | avg/trade | up days | maxDD$ |
|---|---|---|---|---|---|---|
| quoted / 1.04x (was live) | 240 | 50 | $322 | $1.34 | 38% | 1,454 |
| quoted / 1.00x | 245 | 50 | $1,558 | $6.36 | 48% | 813 |
| model / 1.04x | 54 | 31 | $309 | $5.72 | 52% | 724 |
| **model / 1.00x (NOW LIVE)** | **118** | 41 | **$950** | **$8.05** | **59%** | **648** |

Quoted scoring's extra 201 picks were worth $0.97 each -- rank bought by inflated leg mids.
The 1.04x gate killed 80% of model-ranked spreads (the 15:00 leg mid is under 1.04x model on
most good candidates); the 1.00x gate keeps the per-trade edge and doubles the count. Quoted/1.00x
makes more total on 2x the trades, and its $322 -> $1,558 swing on a small gate change is a
fragility flag. `replay_canon.py --select {quoted,model} --gate <m> --tag <s>` reproduces all four.

**NOT yet verified on a real scan** -- first live check is Monday 2026-09-14 09:00
([[feedback-verify-live-changes-next-run]]). On the Sunday adhoc snapshot the new defaults rank 44
and qualify 0 (6 clear GROUND, all 6 below fair on the stale book) -- expected, not a bug.

### Live record 2026-05-27 .. 2026-09-10 (what actually happened, 198 picks/51 days)

Settled by the site, booked at its own **0.80x mid** basis, qty 1:

| basis | before comm | after $1.30 |
|---|---|---|
| as the site books it | $2,870 | **$2,613** |
| + fix 2 bad settlements | $3,374 | $3,117 |
| + backtest 50% partial haircut | $3,051 | **$2,793** |

WIN 54.0% / PARTIAL 14.6% / LOSS 31.3%, $21,460 risked, 13.0% on risk, 32/51
winning days, max drawdown -$1,250. By month: May -$415, Jun -$255, Jul +$949,
Aug +$1,530, Sep +$985. **Seven different config eras in that window** — this is the
record of several strategies, not one.

### Three defects found while checking — none fixed

1. **Two settlements are wrong**, both 2026-06-15, both backfilled late
   (`settled_at` 2026-07-15), both booked LOSS when their own recorded
   `underlying_price` implies WIN:
   - **DE** bull_put 580/577.5 exp 06-18, DE closed **589.24** (verified) -> booked
     LOSS -$122.
   - **ISRG** bear_call 415/417.5 same day, closed **406.78** -> booked LOSS -$114.
   -$236 wrongly booked, ~9% of the record. The other 196 settle correctly.
2. **The live tab does not apply the partial haircut the backtest does.** Live books
   winning partials at full value; the backtest halves them (confirmed exactly 2x on
   CAT, VRTX, ISRG). Worth $324 here. **History and Backtest are not on the same
   basis** — see also the §0.37 caption bug.
3. **`output/daily_closes.parquet` holds only 12 tickers from 2026-08-19 onward**
   (97 before). `live.closes.load_closes()` backfills from snapshots and recovers
   ~89 most days, but **2026-08-21 and 2026-08-28 still have only 12**. Two holes in
   a 252-session window will not break P_real (`MIN_OBS=1`), but the store is
   degraded and wants a proper backfill.

**MMC confirmed dead from a third source:** IBKR `qualifyContracts` returns no
contract for it (also no Yahoo, 0 of 205 September snapshots — §0.36). RUTW and
SPXW also fail, correctly, as index roots rather than stocks.

## 0.39 Old canon vs D_ent canon on the SAME fill basis: m x model credit (2026-09-13) — CURRENT STATE

The old canon's published books were booked at 0.80x the vendor quote (mid in-sample, clamped
LAST for OOT). The D_ent canon is booked at a multiple of the spread's own smile-fit model
credit. Those are not comparable, so the old canon's trade sets were repriced at model-relative
fills and both canons were run through the same equity builder (`report_mid_canon.build_payload`),
with the $1.30 commission and the partial-WIN 50% haircut on every row.

### Method

- **Old canon** = the archived pre-2026-09-11 payloads (`git show 5da76fd~1:live/data/backtest_equity.json`
  and `oot_equity.json`): delta 0.50, band 0.35-0.65, G_rv, rv_vs_iv DKL k=10, thr 0.05,
  top-5/day. IS 2,250 trades (2020-01-07..2025-12-24), OOT 260 trades (2026-01-05..08-13).
- **Selection held fixed** (as §0.37 did for the D_ent book). Each old-canon spread was repriced at
  m x its smile-fit `model_credit`: fits from `research/dkl_2026_09_13/is_synth.parquet` /
  `oot_synth.parquet`, plus 437 IS and 43 OOT chains refit from the vendor year files with
  `ent_canon.fit_smiles` (identical fitter). Priced 2,245 of 2,250 IS and 260 of 260 OOT.
- Settlement uses the payload's own expiry close. The published basis reproduces EXACTLY on the
  matched rows (IS qty2 $81,457 = $81,457; OOT $10,476 vs $10,475).
- **New canon** = `report_ent_canon.select()` with `FILL_MULT` overridden; the 1.08x row
  reproduces the published $38,347 IS / $14,446 OOT (qty1 P&L) exactly.
- Scripts and outputs: `research/fill_basis_2026_09_13/` (`refit_missing.py` first, then
  `old_canon_model_fill.py` for IS and `old_canon_model_fill_oot.py` for OOT; results in
  `old_vs_new_model_fill*.csv`). They pull the archived payloads from git themselves. IS needs
  `/tmp/gepo_pairs.parquet` and `/tmp/gepo_expclose.parquet` from the §0.30 research session.

### The old canon's edge was quote inflation

Old-canon booked credit (0.80x quote) over smile-fit model credit: **IS median 1.23x**
(p10 1.00, p25 1.10, p75 1.44, p90 1.69; every year 1.09-1.29x; every width rung 1.12-1.26x),
**77% of IS trades booked above 1.08x model**. OOT median **1.19x**, 69% above 1.08x. The raw
mid was 1.54x model in-sample. Median credit/width: booked 0.536, model 0.432 at a fitted
short delta of 0.49. Hit rates never move across fill rows (IS 47/18/35, OOT 53/16/31), so as in
§0.37 the entire difference between rows is price.

### In-sample 2020-25, qty=1 on $10k

| book | n | med c/w | qty1 P&L | Sh(wk) | maxDD | qty2 P&L |
|---|---|---|---|---|---|---|
| OLD @0.80x mid, no comm (published) | 2,245 | 0.536 | $40,728 | 2.44 | -5.8% | $81,457 |
| OLD @0.80x mid + comm | 2,245 | 0.536 | $37,810 | 2.34 | -6.4% | $75,620 |
| OLD @1.00x model | 2,245 | 0.432 | -$3,755 | -0.17 | -63% | -$7,510 |
| **OLD @1.04x model** | 2,245 | 0.450 | **$2,582** | **0.29** | **-36%** | $5,165 |
| **OLD @1.08x model** | 2,245 | 0.467 | **$8,909** | **0.76** | **-24%** | $17,819 |
| NEW @1.00x model | 3,999 | 0.503 | $5,116 | 0.41 | -40% | $10,232 |
| **NEW @1.04x model** | 3,998 | 0.523 | **$16,744** | **0.91** | **-20%** | $33,488 |
| **NEW @1.08x model** | 3,997 | 0.543 | **$28,347** | **1.35** | **-15%** | $56,694 |

In-sample the old canon has NO edge at fair value (negative at 1.00x) and the D_ent canon beats
it at every multiple: ~3x the P&L at 1.08x, ~6x at 1.04x, with better Sharpe and drawdown.

### OOT 2026, qty=1 on $10k (new canon cut to the old canon's entry window, <= 08-13)

| book | n | med c/w | win/part/loss | qty1 P&L | Sh(wk) | maxDD |
|---|---|---|---|---|---|---|
| OLD @0.80x LAST, no comm (published) | 260 | 0.504 | 53/16/31 | $5,238 | 3.55 | -6.3% |
| OLD @0.80x LAST + comm | 260 | 0.504 | 53/16/31 | $4,900 | 3.34 | -6.6% |
| OLD @1.00x model | 259 | 0.432 | 53/16/31 | $1,492 | 1.13 | -13.8% |
| **OLD @1.04x model** | 259 | 0.450 | 53/16/31 | **$2,226** | **1.60** | **-12.2%** |
| **OLD @1.08x model** | 259 | 0.467 | 53/16/31 | **$2,960** | **2.05** | **-10.7%** |
| NEW @1.00x model | 530 | 0.507 | 43/18/39 | $1,158 | 0.71 | -13.5% |
| **NEW @1.04x model** | 530 | 0.527 | 43/18/39 | **$2,797** | **1.54** | **-9.9%** |
| **NEW @1.08x model** | 530 | 0.547 | 43/18/39 | **$4,433** | **2.34** | **-6.6%** |

(New canon on its full OOT frame, entries to 08-20: 544 trades, 1.08x $4,446 / Sh 2.34 / DD -6.6%.
The featATM6 OOT frame ends at 08-20 entries although the payload window label says 09-11.)

Out of sample the picture is much less lopsided. **At 1.08x the new canon wins on every metric.
At 1.04x it is close to a tie** (new makes more dollars on twice the trades, old has marginally
higher Sharpe and worse DD, and earns more per trade). **At 1.00x the old canon is ahead** and,
unlike in-sample, stays positive at fair value: its 2026 book carries a genuine hit-rate edge
(53% wins vs 43%) that the D_ent book does not have, so it depends less on the fill.

### Verdict (user + assistant, 2026-09-13): D_ent stays the deployed canon, provisionally

- On honest pricing the old canon's published numbers are not evidence of anything; the D_ent
  numbers are the only ones the live book can be held to.
- The advantage is entirely a function of the achieved fill multiple. >= 1.06x: closed case for
  D_ent. 1.00-1.04x: roughly equal on the evidence, and the old canon's hit-rate edge starts to
  matter. The 1.04x execution gate (§0.37) is the control that keeps the live book out of the
  1.00x rows.
- Two things decide it: the median live **fill/model ratio over a few weeks of real fills**
  (Actuals tab, §0.37), and the **May-August IBKR replay** staged in §0.38 and still unrun.
- Caveat: old-canon selection was NOT rescored on model credit. Its picks were chosen partly
  because the quote looked rich, so a version scored on honest prices might do somewhat better
  than the rows above. Doable from the picks cache if wanted.

## 0.40 First live day on the model-ranked canon (2026-09-14) — CURRENT STATE

Ten commits, all deployed to both Mya checkouts and verified by curl.

**Open-day bug, fixed (`b792faa`).** The 09:00 and 09:30 scans ranked
`live/snapshots/adhoc/merged.parquet` — a directory left by Sunday's manual runs —
because `_latest_snapshot()` sorted directory names and "adhoc" > "2026-09-14". The
live page showed Sunday's numbers at the open. Directory removed; the picker now
only accepts date-named directories. **Never leave ad-hoc files under `live/`**
([[feedback-readiness-means-run-the-pipeline]]).

**Ranker / gate**
- `df32976` gate compares the IBKR credit to the walk-away at 2dp half-up: INTC quoted
  0.275 (shown 0.28) vs walk-away 0.28 read as a tie and failed. A tie now qualifies.
- First live scans under model ranking: 09:34 → 29 ranked / 1 qualified; 10:00 → 40 / 3.
  Intraday repeat rate is by design (12 scans, 11 distinct names, INTC 7/12); it rotates
  day to day (21–32 distinct names on full prior days).

**Assignment / pin (`26ab18f`, `b97576b`, `369abb3`)**
- Early-exercise channel disabled (it fired 10:01 Monday on a CSCO carry test).
- Telegram alert file written only for positions expiring TODAY.
- Yellow pin highlight on Actuals, History and Snapshots — `pin_open()` in webapp — ONLY
  on the pick's settlement day (Friday, or Thursday on a Thursday-settle week), spot
  inside the strikes inclusive, not yet settled. Nothing lights up Mon–Thu.

**Live tab (`1f7d058`, `800160c`, `d1913bc`)**
- Ranked label: "All ranked candidates · tick H:MM AM ET".
- `/api/latest.json` no longer swaps to the frozen payload after 15:01; the tab follows
  every scan to the 16:00 close. `?frozen=1` returns the frozen payload. History unchanged.

**Snapshots tab (`4f0aa51`)**: 7 most recent days by default (was 15); "show 7 more" / all.

**INTC half-wide question:** the 97.5/97 Sep-18 put spread is real — the Sep 18 weekly
has half-dollar strikes (97.5 bid 3.40 / ask 3.60, Δ 0.54, vol 101); TWS's $1-wide combo
grid on the Sep 14 expiry does not show it. Half-wides are allowed by config; one line
to forbid them if wanted.

## 0.41 CLOSED — delta-matched P_real (2026-09-15): tested in §0.42, exact strikes stay canon

**User's idea, to test on the featATM frame.** P_real today counts, over the trailing 252
sessions, how often the stock moved more than TODAY's percentage distance to the exact
strike in d days (`ent_canon.p_real`, thresholds `ths`/`thl` = strike/spot − 1). That
distance is set by today's IV, so a calm history flatters the pick and a wild history
punishes it regardless of whether the stock's *tails* are actually fat.

**Delta-matched variant:** on each historical day t, put the short strike where a 0.55Δ
strike *would have been* — i.e. threshold_t = z·σ_t·√(d/252) with z = Φ⁻¹ of the delta
and σ_t = that day's vol — and count crossings of THAT. P_real then measures "does this
name's realized distribution have thinner tails than the market prices" (the VRP thesis
directly) instead of "will spot reach this specific level."

Expected effects: removes the vol-regime bias; dampens the raw directional-drift term
(the thesis leans on drift, so watch that trade-off).

**How to run it (MacBook, has the frame):**
- σ_t: vendor daily ATM IV per (ticker, date) from the year files if convenient, else
  trailing 20-day realized vol from closes — try both; the RV version is what the mini
  could run live (IBKR closes only).
- Same selection, same fill (1.08x model), only P_real changes. Report: pick overlap vs
  current canon, per-trade P&L, Sharpe, maxDD, and the bull_put share (does the drift lean
  shrink?). IS 2020-25 and OOT 2026 separately.
- Suggested home: `research/dkl_2026_09_13/p_real_delta_matched.py`, reusing
  `report_ent_canon.select()` with a swapped `p_real`.

**Not started on the mini.** No code changed for this.

### Also recorded 2026-09-15 (all deployed, all in main)

- Actuals rows added from the LIVE tab (source kind "live") are now marked every scan
  (`track_frozen._track_live_actuals`) and settled at expiry
  (`snapshot_picks._settle_live_actuals`); `upload_to_mya.sh`'s merge is field-aware
  (mini's marks/settlement as base, Mya's `actual_credit`/`actual_max_loss` on top) —
  the old "remote wins wholesale" discarded the mini's marks on every upload.
- **Tracker marks are floored at intrinsic** (`track_frozen`, `fdd5adc`). ISRG 370/372.5
  bear call, spot 377.31, marked $0.68 from per-leg IVs backed out of a 3-point-wide book;
  now $2.50. IBKR's own P&L uses mid-of-closing-quotes with no floor and flatters deep-ITM
  legs (AAPL 332.5/330 marked 1.22 vs 2.26 intrinsic) — the site is the conservative one.
- Live tab: `+` on every ranked row (`/api/actuals/from_live/<i>`, saves the live row;
  snapshot linkage only from the same scan); blue `+` on live/History/Snapshots when the
  same option is already held; held rows stay white/grey. Portfolio-totals card and top-5
  cards removed. Ticker strip reads `put` / `call`. Bright = first five qualified rows.
- Actuals display basis = IBKR credit (combo mid > fresh combo last > leg mids — the credit
  priority was flipped from last-first, `358d7e3`); no auto-filled fill on add; min/target
  on Actuals use the persisted `model_credit` (snapshot picks now store it; 24 rows
  backfilled) so they match the live tab.
- IBKR "cannot have open orders on both sides" = a resting close order on the same
  contract, not a size limit.
- Pin highlight only on the pick's settlement day; assignment Telegram only for positions
  expiring today; early-exercise channel disabled.

## 0.42 Live P_real is IBKR-ONLY; delta-matched P_real tested and rejected (2026-09-15) — CURRENT STATE, ACTION NEEDED ON THE MINI

### Why

Three different close series were feeding the same P_real formula and nobody had compared them:

| series | what it is | sessions/yr | IS 2020-25 Sh(wk), canon selection |
|---|---|---|---|
| research frame (behind the published 1.35) | vendor underlying price on candidate days + expiry closes; a stock has a price only on days it produced a candidate | ~226 (10% of sessions MISSING) | **1.35** (published) |
| `output/daily_closes.parquet` raw | vendor `UnderlyingPrice` per Symbol/DataDate incl. the vendor's HOLIDAY REPUBLISHES (stale duplicate close on ~9 non-session dates a year; AAPL 2022-07-04 == 07-01 exactly; 56 such dates 2020-25) | ~260 | 1.07, DD -27% |
| same, SPY sessions only | the honest full-session series | ~251 | **1.20**, DD -15.9% |
| Mac mini live (`load_closes` before this change) | 2026 vendor store + Yahoo backfill + 15:31 snapshot prints | mixed | never measured |

`research/dkl_2026_09_13/p_real_delta_matched.py` (exact-strike recompute on the frame's own
series reproduces the frame's p/q/ro to 0.0000). Median |dp| between series is < 0.01 but
GROUND ranks within a day on tiny margins: 18% of picks change and Sharpe moves 1.35 -> 1.20.
**The published 1.35 is in-sample luck from a defective calendar; 1.20 / DD -15.9% is the
honest canon figure on a full-session series.** The frame series has no lookahead (every
price is past when used), it is just not something the live path can or should reproduce.

### Delta-matched P_real (§0.41) — tested, does NOT help

Threshold on historical day t = today's % distance to each strike x sigma_t / sigma_today
(same selection, credit 1.08x model, D_ent, k, thr). Both sigma sources, both close series:

| P_real variant | frame series | full-session series |
|---|---|---|
| canon exact strikes | 1.35 / DD -14.9% | 1.20 / -15.9% |
| delta-matched, ATM IV (sig0 ffilled) | 1.31 / -14.7% | 1.20 / -13.9% |
| delta-matched, RV20 | 1.19 / -19.2% | 1.12 / -18.9% |

ATM-IV is a wash (slightly lower DD both times), RV20 is worse on both. bull_put share does
not shrink (83% vs 79%).

OOT 2026 (544 trades, 01-01..08-20) flips the ranking:

| P_real variant | frame series | full-session series |
|---|---|---|
| canon exact strikes | 2.34 / DD -6.6% | 1.75 / -10.5% |
| delta-matched, RV20 | 2.66 / -5.1% | 2.20 / -8.6% |
| delta-matched, ATM IV | 2.18 / -8.2% | 1.58 / -8.7% |

RV20 wins OOT after losing IS; ATM-IV loses OOT after tying IS. Each variant is better in one
window and worse in the other and the two sigma sources disagree in both: noise on 540 trades,
not a method. **DECISION (user, 2026-09-15): exact strikes stay canon.** Delta-matching is
closed. Reasoning that settled it: P_real scores a strike as a PERCENTAGE move from spot, not
a price level, so "uncharted territory" (all-time highs) is not a problem; and the vol-regime
bias it was meant to remove runs the safe way — by today-IV / trailing-252d-RV quintile, P_real
falls 0.467 -> 0.406 from the lowest to the highest quintile while the realized loss rate
stays 41-45%, so a spiking IV makes P_real more pessimistic, not more confident. The only real
"uncharted" case is thin history (a name with few sessions), which is a data-depth issue the
two-year IBKR seed addresses. §0.41 is CLOSED.

**Expiry-matched P_real (user, 2026-09-15): also tested, also rejected.** P_real from ONLY the
moves that end on the past 52 (or 104) weekly expiry days (last session of each ISO week: Friday,
Thursday on holiday Fridays), each over the candidate's own DTE, instead of all 252 overlapping
daily windows. `p_real_expiry()` in the same script; logs `p_real_expiry_{IS,OOT}_{frame,store}.log`.

| P_real | IS frame | IS full-session | OOT frame | OOT full-session |
|---|---|---|---|---|
| canon 252 sessions | 1.35 / DD -14.9% | 1.20 / -15.9% | 2.34 / -6.6% | 1.75 / -10.5% |
| 52 expiries | 0.89 / -36.3% | 1.18 / -30.0% | 1.92 / -4.5% | 2.42 / -5.3% |
| 104 expiries | 1.03 / -34.2% | 1.40 / -23.1% | 2.21 / -5.2% | 1.82 / -10.3% |

Pick overlap with canon only 45-49%. 52 observations make P_real noisy and Kelly EV on a noisy
p picks outliers: the 2022 drawdown blows out to -30..-36% on every series. The one good cell
(52w OOT full-session) is 583 trades in a window with no 2022. 252 sessions stays canon.

Logs `p_real_delta_matched_{IS,OOT}*.log`; per-candidate p/q/ro for every variant in
`p_real_delta_matched_{IS,OOT}_{store,frame}.parquet` (gitignored). IS runs in ~2 min, OOT
in ~15 s, with month progress and a to-date table after every year.

### The change: `live/closes.py` reads ONLY `output/ibkr_closes.parquet`

- **`live/fetch_ibkr_closes.py`** (NEW): official daily TRADES closes from IBKR into
  `output/ibkr_closes.parquet`, MERGE only, read-only, client id 178, 6 s pacing.
  `--years 2` seeds (~10 min), `--days 10` tops up. Universe = SP100 + snapshot equities +
  SPY (for the calendar); index roots and MMC skipped. Today's bar is dropped before 16:05 ET.
- **`live/closes.py`**: `load_closes()` = IBKR store + snapshot prints ONLY for dates after
  the store's last date (stop-gap for a missed top-up). The vendor store is no longer read
  by the live path (`STORE` kept for `build_daily_closes.py`). `session_calendar()` = dates
  where >= 50% of tickers have a bar; `fill_gaps()` forward-fills a single ticker's missing
  SESSIONS up to 3 in a row (an IBKR hiccup), never adds a non-session date. **Holidays are
  not duplicated** — that is the 1.20-vs-1.07 difference above, user chose the higher Sharpe.
  `GEPO_IBKR_CLOSES` env var overrides the store path (used by the offline test).
- **`live/cron_daily_bars.sh`**: runs `-m live.fetch_ibkr_closes --days 10` after
  `fetch_daily_bars` at 17:01.
- **`live/ranker.py`**: logs `closes: IBKR store N sessions, T tickers, first -> last`, or a
  loud WARNING when the store is empty (P_real then runs on snapshot prints only).

Offline test on the MacBook (no IB Gateway here): a scratch store built from the vendor
closes on SPY sessions with punched holes; calendar has 0 weekend/holiday dates, a 2-session
hole is filled, a 6-session hole fills 3 then stops, snapshot rows appear only after the store
end. Ranker on `live/snapshots/2026-08-19/1531`: same 2 qualified picks (ISRG 400/402.5,
RTX 222.5/220) under the new and old loaders, median |dp| 0.004 on 44 common candidates.

### TO DO ON THE MAC MINI (in this order)

```
git pull --ff-only origin main
python3 -m live.fetch_ibkr_closes --years 2          # ~10 min, IB Gateway up, read-only
python3 -c "from live.closes import closes_status as s; print(s())"   # expect ~96 tickers, ~500 sessions
python3 -m live.ranker --snapshot live/snapshots/<latest>/<hhmm>.parquet   # look for the 'closes: IBKR store' line
```
Then the 17:01 cron keeps it current. Verify the store against the vendor on a few
overlapping 2026 dates before trusting it (§0.36 caught BDX's adjusted-close problem
that way; IBKR TRADES closes are unadjusted, which is what P_real wants).

### Wagering block on the Backtest and OOT tabs (2026-09-15, deployed to Mya)

`webapp._wagering()` computes, at render time from the payload's trade list (JSON untouched),
per contract qty=1: yield on risk, turnover, simple annual return (yield x turnover, no
compounding), positive/negative split with partials by their actual sign, fill/fair (model)
ratio, the price edge as % of risk after commission (the closing-line-value analogue), and
realized P&L over that price edge. `live/templates/_wagering.html`, included on both tabs
above the charts. Published books: IS yield 10.3%, turnover 5.1x, simple 51.9%/yr, 53.0/47.0,
price edge +6.9% of risk ($4.76/contract), realized/edge 1.49x. OOT 11.2%, 5.7x, 64.4%/yr,
51.8/48.2, +7.1% ($5.18), 1.58x. Since the books are booked at 1.08x model by construction,
the CLV cell is the assumption the result rests on; hold it against the live fill/model
ratio on Actuals (18 real fills: median 1.111).

**gunicorn HUP on Mya:** the master's parent is NOT pid 1 (it sits under a shell), so the
`awk '$2==1'` one-liner in the archive finds nothing and kills nothing. Find the master as
the gunicorn pid that other gunicorn pids have as ppid:
```
G=$(ps -eo pid,ppid,cmd | grep "[g]unicorn" | grep "live.wsgi"); M=$(echo "$G" | awk '{print $1}' | while read p; do echo "$G" | awk -v p="$p" '$2==p' | grep -q . && echo "$p"; done | head -1); kill -HUP "$M"
```

### Still open

- The live ranker still TRIES to read `output/iv_rank.parquet`, `output/rv_table.parquet`,
  `data/spy_us_d.csv` (regime), earnings and dividend calendars. Regime and vol gate are
  OFF and DKL is entropy, so these are dead for scoring; the reads are try/except. Not
  removed, not verified unused downstream.
- Rerun the IS backtest on the actual IBKR series once the mini has it, so the number
  the live book is held to is computed on the series the live book uses.

## 0.43 CANON CHANGE: k=4, thr 0.005, P_real on full-session closes (2026-09-15) — CURRENT CANON

**What changed (user decision 2026-09-15, "proposed win. lets change to that"):**

| | old canon (§0.35) | NEW canon |
|---|---|---|
| P_real close series | research frame: candidate-day prices only (20-35% of sessions missing, §0.42) | full-session: every trading day (vendor store on SPY sessions for the backtest; the IBKR store live) |
| GROUND threshold | 0.01 | **0.005** |
| k (exp(-k·D_ent)) | 1.0 | **4.0** |
| everything else | unchanged: 0.55Δ (0.50-0.60), exact-strike P_real over 252 sessions, D_ent, smile-fit credit, fill 1.08x, top-5/day, $1.30 commission | |

Constants: `ent_canon.K/THR`, `config.GROUND_THRESHOLD`, `ground.DKL_K`. `ent_canon.backtest_closes()` is the
backtest's full-session series; `report_ent_canon.select()` now RECOMPUTES p/q/ro/EV on it instead of taking
the frame's. Payloads regenerated (numbers reproduce `research/dkl_2026_09_13/expiry_k_sweep.log` exactly);
`_fill_sensitivity.html` regenerated for the new books. Deployed to Mya.

**Published numbers, qty1 on $10k, fill 1.08x:**

| | old canon (frame series) | old cell on full-session (honest) | **NEW canon** |
|---|---|---|---|
| IS 2020-25 | 3,997 tr · $28,347 · yield 10.3% · Sh 1.35 · DD -14.9% · Calmar 1.87 | 3,999 · $24,422 · 8.9% · 1.20 · -15.9% · 1.59 | **4,552 · $30,655 · 9.5% · 1.37 · -16.1% · 1.82** |
| OOT 2026 | 544 · $4,446 · 11.2% · 2.34 · -6.6% · 10.7 | 547 · $3,058 · 7.7% · 1.75 · -10.5% · 4.5 | **594 · $4,372 · 9.9% · 2.62 · -4.2% · 16.3** |

qty2: IS $61,310 / DD -20.7%, OOT $8,744 / -7.1%. ~830 trades/yr (was ~730). Bull-put share 77% IS / 80% OOT.
Every IS year positive at the new cell (old cell lost 2023): 2020 $4.6k, 2021 $6.0k, 2022 $2.8k, 2023 $1.1k,
2024 $9.6k, 2025 $6.6k. Fill sensitivity: at 1.04x IS Sh 0.91 / OOT 1.63 (old cell 0.75 / 0.85); break-even
~0.985x model in both windows; ~$6,500 IS P&L (qty2) per 1% of model credit.

**Why this cell.** On full-session P_real the canon's k x thr grid (`expiry_k_sweep.log`, fill 1.08) has a
plateau at thr 0.005, k 3-6 that holds in BOTH windows: IS Sh 1.28-1.37 / Calmar 1.35-1.82, OOT Sh 2.62-2.96 /
DD -4.2..-4.8 / Calmar 15.8-16.6. k=4 is its middle. Neighbours are all good; it is not a spike.
**Caveat, stated on the OOT tab:** 2026 was used to confirm the cell, so it is no longer a clean holdout for
this canon. The plateau is the defence.

**Alternatives tested and rejected the same day (all on full-session closes):**
- 52-expiry P_real (moves ending on the past 52 weekly expiries): best single OOT cells (thr 0.005 k=2: 2.89)
  but IS drawdown -23..-31% at EVERY k; the user's candidate cell (thr 0.005, k=8) beats the new canon on
  IS Sharpe by 0.06 (1.43 vs 1.37) and loses on the other eight metrics (IS yield 7.5 vs 9.5, DD -24.6 vs
  -16.1, Calmar 1.07 vs 1.82; OOT 2.46/-6.5/9.5 vs 2.62/-4.2/16.3), and is a spike (k=4 1.19, k=6 1.35, k=8
  1.43, k=12 1.28).
- 104-expiry P_real: best single IS cells (thr 0.005 k=2: 1.65) but OOT never above 2.0 with DD -13..-15%
  at every k, Calmar 3-4 vs the canon's 8-16.
- Delta-matched P_real (§0.41/0.42): closed.
- Bear calls lose money in every variant in both windows (IS yield 1-3%, OOT -3..-23%); the books differ
  mainly in how many they take. Not acted on; a separate question.

**Live DTE window narrowed to 0-5 (user, 2026-09-16; was 0-6):** `live_config.LIVE_DTE_MAX = 5`. P_real clamps
DTE to 1-4 sessions, so a spread beyond the same-week expiry was being scored on the wrong move length. 5 still
admits a holiday-shifted same-week expiry. Applies to the fetcher's chain request and the ranker's filter.

**Live:** the ranker reads `ground.DKL_K` and `config.GROUND_THRESHOLD`, so the mini picks this up on
`git pull`. Offline on `live/snapshots/2026-08-19/1531` with a full-session test store: 4 qualified
(RTX, ISRG, MS, FCX) vs 2 under the old cell — the lower threshold passes more names; the 1.00x execution
gate still applies on top. Expect ~15% more picks/day.

## 0.46 NEXT RESEARCH: 10 option-market ideas for 1-4 day direction (2026-09-16) — for the MacBook

**Why this list.** Price-chart TA is exhausted for this book (§0.45 and research/ta_direction/): 508 veto rules and
280 ranking tilts did not beat a shuffled-data null, a walk-forward ridge on ~70 features had out-of-sample corr
~0.01, price features flip sign by regime, and the opening gap (own or market) has no within-day power. The user
wants a real directional edge. The information most likely to carry one at 1-4 DTE on mega caps is in the OPTIONS
market. Rankings below are Claude's judgment; **none of these has been tested.**

**Data check (verified 2026-09-16).** Backtest vendor chains (`output/<year>_sp500_last.parquet`) carry, per strike
and expiry per day: Bid, Ask, Last, ImpliedVolatility, Delta, Gamma, Vega, Theta, **OpenInterest**,
UnderlyingPrice. **No option Volume in the backtest.** Live snapshots (`live/snapshots/<date>/*.parquet`) add
Volume, BidSize, AskSize. `data/earnings_calendar.csv` exists; whether it holds 2020-25 history is UNVERIFIED.

### From the option chain (backtestable)

1. **Open-interest pin toward expiry.** Near expiry, price tends to drift toward strikes with very large open
   interest (dealer hedging). Signal: distance and direction from spot to the heaviest-OI strike within +-1 ATR
   for the expiry being traded. Fits 1-4 DTE Friday expiries directly. Needs OI by strike.
2. **Dealer gamma sign (GEX).** Net dealer gamma per name from sum(OI x gamma x 100 x spot) by strike, with a sign
   convention for calls vs puts (document the assumption). Long dealer gamma -> mean reversion; short -> moves
   extend. Use it to decide whether P_real should expect reversal or continuation of the recent move.
3. **25-delta risk reversal and its day-over-day change.** Put IV minus call IV at 25 delta; a steepening put skew
   signals downside demand. §0.19 found it significant on 2026 and never validated it on 2020-25. Run §0.19's
   validation first, then the incremental test over GROUND.
4. **Call-vs-put IV gap at the same strike.** By parity, call and put IV at one strike should match; call IV richer
   than put IV -> bullish informed demand, and vice versa. Use ATM or near-ATM strikes with both sides quoted.
5. **Implied skewness from the canon smile fits.** `ent_canon.fit_smiles` already fits a smile per chain; its slope
   and curvature define a skewed risk-neutral distribution. Blend that skew into P_real instead of assuming the
   last 252 moves repeat.
6. **IV term structure.** Front-expiry ATM IV vs the next expiry's; inversion (front richer) means an imminent move
   is priced, usually downside -> shift drift down or avoid bull puts.
7. **Day-over-day change in open interest by side.** New put OI below spot = hedging/bearish; new call OI above
   spot = bullish. Needs consecutive days of OI by strike.
10. **IV rising with price.** Normally ATM IV falls in a rally. IV up while the stock is up = paying for protection
    (bearish); IV down while the stock falls = complacent (bullish). Needs ATM IV per day.

### Events and flow

8. **Post-earnings drift.** After an earnings gap, stocks tend to keep drifting with the gap for days; trade only
   with the gap in the 1-5 sessions after earnings. Needs HISTORICAL earnings dates for 2020-25 (check the
   calendar file first; if it is forward-only, get a history before testing).
9. **Live order-book imbalance.** Bid size vs ask size and call vs put volume at the 15:01 scan. Live data only, so
   NO backtest: run as a logged shadow signal to build a track record.

### Test protocol (mandatory — this is what went wrong on 2026-09-16)

1. **Prediction first, per year:** rank correlation of the signal with the stock's own move to expiry, both pooled
   and WITHIN each day (cross-sectional). A signal must show within-day power in most of 2020-2025. Pooled-only
   power is just the market move (the gap's failure).
2. **Chance baseline:** shuffle the signal across stocks within each date (for stock-level signals) or shift the
   date sequence (for market-level signals); the real result must beat the 95th percentile of the null best.
3. **Book test, walk-forward only:** any fitted parameter for year Y is fitted on data before Y (as `fit_gap.py`).
   Never replay past years with a later fit.
4. **Decompose every book change:** trades unchanged / swapped by side flip (direction) / swapped by other stock
   (selection), with P&L per year. Report win % AND win+partial %. Say whether one year carries the result.
5. **2026 is the only unseen data; check the mechanism there too,** not just the P&L.

The user also asked ChatGPT for 10 ideas with a structured prompt (same context and failed list); merge that list
here before testing and drop anything already covered.

## 0.45 CANON: own-gap drift in P_real (2026-09-16)

**What it is.** TA as a prediction feeding the belief (user, 2026-09-16), using ONLY the traded stock's own data.
For each candidate: gap = (today's open / prior close - 1) / (prior 14-day Wilder ATR / prior close);
z = (gap - mean_Y) / sd_Y clipped +-4; drift mu = GAP_GAMMA (1.0) x beta_Y x z x sigma_d x sqrt(clip(DTE,1,4)),
sigma_d = std of the stock's last 20 daily log close changes ending the session before entry. Every historical
move behind that stock's P_real is shifted by mu before it is counted, so G, GROUND, threshold, side and top-5 follow.
`GAP_GAMMA = 0` reproduces the §0.43 canon exactly (verified).

**History, same day.** A MARKET-average version (mean gap over 83 names) was canon for a few hours (commits
888bc59, 016432e). The user then decided the drift must not depend on the market at all. That version is removed
from code; its research is in research/ta_direction/ta_predict_mkt.py, ta_gap93.py, ta_gap_sp100.py.

**Walk-forward fits** (`fit_gap.py`, one row per stock/day/DTE before year Y): beta 2021 0.113 (5 months of data),
2022 0.022, 2023 0.014, 2024 0.020, 2025 0.012, 2026 0.026. **Each January: `python3 build_name_gaps.py` (writes
.new if the parquet exists; rename after checking), `python3 fit_gap.py <year>`, paste into `GAP_FIT`.**

**Published (report_ent_canon.py, qty1):**

| period | pre-gap P&L | own-gap P&L | pre-gap Sh | own-gap Sh | pre-gap DD | own-gap DD |
|---|---|---|---|---|---|---|
| 2021 | $6,009 | $8,687 | 1.34 | 2.11 | -16.7% | -10.7% |
| 2022 | $2,785 | $2,905 | 1.11 | 1.09 | -20.4% | -28.0% |
| 2023 | $1,067 | $1,584 | 0.50 | 0.65 | -21.3% | -21.9% |
| 2024 | $9,559 | $8,933 | 2.30 | 2.32 | -11.5% | -10.6% |
| 2025 | $6,589 | $5,702 | 1.55 | 1.42 | -14.4% | -12.5% |
| IS 2020-25 | $30,655 | $32,456 | 1.37 | 1.51 | -16.1% | -12.9% |
| 2026 | $4,372 | $4,591 | 2.62 | 2.97 | -4.2% | -4.0% |

**Evidence against it — recorded because the user chose to keep it anyway (do not present it as an edge):**
- Own gap vs own move to expiry, rank IC: pooled +0.12/+0.04/+0.01/+0.06/+0.01/+0.04 (2020-25), **-0.02 in 2026**.
  WITHIN a day it is ~0 every year: a stock gapping more than others does not do better. The pooled positive is the
  market component every stock's gap carries.
- Nearly all the P&L gain is 2021 (+$2,678, fitted on 5 months). 2022-25 alone: **-$876** vs pre-gap; worse in 2024
  and 2025; 2022 drawdown -28%.
- The market-average version was stronger in-sample but its day-level correlation in 2026 was also negative (-0.07).

**Live path.** `live/fetch_name_gaps.py` (read-only IBKR, client 179, ~6 s pacing; universe = live.fetch_ibkr_closes
.universe()) at 09:36, retry 10:06 for names still missing -> `output/name_gaps_live.parquet` (merge). Today's open
comes from IBKR's in-progress daily bar, else today's first 30-minute bar — **NOT yet run against IBKR; check the
first log.** `live/ranker.py` logs how many candidate names have today's gap; the rest get zero drift. Backtest
gaps: `build_name_gaps.py` -> `output/name_gaps_backtest.parquet`.

**Mya.** Payloads uploaded directly from the MacBook (only the two JSONs; `live/NOT_PRODUCTION` still blocks the full
upload). Backups on Mya: `live/data/*.json.bak_pregap_20260916-161954` (pre-gap canon) and
`*.bak_marketgap_20260916-171002` (market-gap version).

**Data defect found.** `data/daily_bars_yahoo/*.csv` has no bars 2026-01-01..2026-05-14; any 2026 TA feature built from
those files alone is invalid. Files not modified; the builders fill from the Yahoo chart API.

**Bugs caught during the build (fixed before publishing):** NaN drift made P_real count every trade as a sure win;
`gap_drift`'s gamma default was bound at import time.

## 0.44 NEXT RESEARCH: directional win-rate work (2026-09-16) — for the MacBook

**Why this is the lever.** Win rate is exactly delta-implied: 43.8% actual against
45.0% implied on the 4,552-trade IS book. There is no directional information in the
picks today. On a near-1:1 payoff (avg WIN +$79.43, avg LOSS -$69.20 per contract),
moving **1% of trades from LOSS to WIN is worth ~$13,500 at qty 2** — slightly MORE
than 1% of fill credit (~$11,600). A +3pt win rate roughly doubles the edge.

Realistic target is +2-3 points, not +10. The goal is removing obvious bad setups,
not calling direction.

### Already ON — do not "fix" these

Verified from a live scan log 2026-09-16, not from a grep:

- **Earnings gate is LIVE** at `live/ranker.py:242` (its own block, reads
  `data/earnings_calendar.csv`). It dropped 2 candidates on every scan that day.
  `spreads.EARNINGS_FILTER = False` is the BACKTEST flag, flipped by `run.py` — it
  does NOT describe live behaviour. Calendar refreshes Fridays 17:01
  (`cron_calendar_refresh.sh`); thin in September by nature (7 names), fills out
  mid-October.
- **Ex-div gate** is live in the same path (drops 5-12 candidates a scan).

### 1. IV skew / 25-delta risk reversal — HIGHEST PRIORITY

Already written up in §0.19: measured and significant on 2026 OOT, **never validated
on 2020-2025**, nothing in canon uses it. That validation is the single highest-value
piece of directional work outstanding and the frame to run it on already exists.

Run it as §0.19 specifies, then the test that section never got: **incremental lift
over GROUND**, i.e. does skew add anything once GROUND has already ranked, or is it
picking the same trades? Report win rate and per-trade P&L for the current canon vs
canon+skew on IS and OOT separately.

### 2. Spike-reversal filter — cheap, mechanical, motivated by a real loss

**The observation (2026-09-16).** The whole energy complex jumped ~3% on 09-15 and
fully round-tripped the next day: EOG 148.54 -> 153.74 -> 145.12 (-5.6% intraday,
below where it started two days earlier), XOM -3.2%, CVX -2.7%. The book had sold EOG
152.5/150 puts near the top; that position went to max loss.

**Rule to test:** skip a `bull_put` when the underlying's prior-session move was
> k sigma IN YOUR FAVOUR (up), and a `bear_call` when it was > k sigma down.
Sigma = the same trailing-20d realized vol the strike band already uses. Sweep
k in {1.0, 1.5, 2.0, 2.5} and also test "no filter" as the control.

Data needed: `output/ibkr_closes.parquet` live, the full-session close series in the
backtest (`ent_canon.backtest_closes()`, §0.43). Report trades dropped, win rate
delta, per-trade P&L delta, IS and OOT separately. Reject unless it holds in BOTH —
§0.41/§0.42 is the cautionary tale: delta-matched P_real won one window, lost the
other, and was correctly rejected as noise.

### 3. Sector relative strength — same motivation, broader

The EOG loss was sector-wide, not idiosyncratic: three energy names moved together
both days. A per-sector one-day move (or the sector ETF: XLE, XLF, XLK, XLV ...) is
the natural generalisation of #2 and would catch the case where the individual name's
move looks unremarkable but the whole sector just spiked. Needs a sector map, which
the repo does not currently carry.

### Explicitly NOT worth testing

- **Short-horizon price momentum** on large caps at 1-4 DTE. §0.19 already ranked
  RM_20 / MOM_5 below skew, and at this horizon it is mostly noise.
- **The regime gate** (SPY vs 100d SMA). Too slow for a 2-day holding period; it is
  already OFF live and the books show no benefit.

### Context for judging any result

The current environment is paying less than the backtest period: live model
credit/width is **0.469 against the backtest's 0.502** (§0.38), and that ~3-point gap
is roughly the entire edge. A directional improvement that only shows up in a rich-
premium regime is not a result. Test both windows, always.

# 🗃️ HISTORICAL ARCHIVE — NOT A CURRENT TASK LIST

The remainder records prior incidents, repairs, experiments, and once-pending work. It intentionally preserves historical detail, including instructions that were correct at the time but are now completed or obsolete. **Do not execute or report an item below as current unless the START HERE block or §0.14 explicitly carries it forward.**

## 0.13 HISTORICAL: ops repair (2026-08-24) — OOT Aug 20 + History 15:31 top-up

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

## 0.12 HISTORICAL: production host status (2026-08-19) — live ops moved toward Mac mini

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

---

## 0.23 DKL works at MARKET credit — §0.22's negative result was an artifact (2026-09-13)

**Read with §0.22.** The 360-cell search in §0.22 booked every trade at a SYNTHETIC
credit of `1.1 x width x (q_emp + ro_emp/2)`. IV never entered the credit, so any
divergence against Q_iv had nothing to act on. Re-run in the real canon world
(market mid credit, 0.80x fill, band 0.10-0.30, OTM<=5%, width<=2.5, OI>=100,
bid>0, G on the empirical (ticker,$width) triple, 52-expiry window, top-5/day,
thr 0.05), the canon `D(P_emp || Q_iv)` earns k monotonically:

| k | IS n | IS final | IS Sh | IS DD | IS loss% | OOT Sh | OOT DD |
|---|---|---|---|---|---|---|---|
| 0 | 3373 | $38.0k | 2.21 | -9.1% | 13.7 | 2.36 | -6.2% |
| 8 | 1677 | $43.6k | 2.80 | -4.3% | 12.3 | 4.38 | -3.2% |
| 16 | 1123 | $41.5k | 3.06 | -1.7% | 10.8 | 4.22 | -1.4% |
| 24 | 839 | $38.0k | 3.10 | -1.1% | 9.5 | 4.46 | -2.1% |

Sharpe rises with k in every IS year (2022: 0.00 -> 3.11; 2023: -0.01 -> 4.60).
Survives fill stress to 0.60x mid (k=0 loses money, k=16 Sh 2.6).

- **Baseline is $38k, not $77k.** The $77,417 on Mya came from MIN_CREDIT_RATIO=0.30
  selecting zero-bid quotes (§0.22). Clean real-credit k=0 is $38k / Sh 2.21.
- **Raw loss rate is FLAT across D(P_emp||Q_iv) quintiles.** What rises monotonically
  is realized-minus-P_emp-predicted loss share (2.8pp -> 8.8pp). It measures how
  over-optimistic the history triple is, not how risky the trade is.
- **`D(Q_cert || Q_iv) = -ln(p_iv)`** (market's surprise at a WIN) IS monotone in raw
  loss (IS quintiles 9.0/11.3/11.6/11.7/11.6%, OOT 8.0/11.0/12.0/13.1/13.2%) and
  gives the same book quality at k=3: IS Sh 2.96 / DD -1.9% / $41.8k, OOT Sh 4.77.
  Its scale is ~7x larger, so k in 2-4 corresponds to k 12-24 above. Not double
  counting: G is on P_emp, Q_iv is new information to GROUND.
- **Equal-trade-count control:** k=0 with the EV threshold raised to match n gives
  IS Sh 2.73-2.87 and OOT Sh 2.4-2.8; k>0 at thr 0.05 gives IS 2.8-3.1, OOT 4.2-4.5,
  and 2pp lower loss rate. The penalty carries information beyond EV selectivity,
  mostly out of sample. Trade count falls ~65-75%: much of the IS gain is the
  threshold refusing days where market and history disagree.
- **Pure re-ranking (thr=0, always 5/day)** is NOT monotone and collapses IS past k~12.
  Do not run DKL without the threshold.
- 60% of candidates are dropped at the G stage: 55% negative Kelly EV (correct),
  5% because the ticker cell has ZERO losses (q=0 -> G=None). Those q=0 names lose
  6.4% realized, the SAFEST group. Shrinking the ticker triple toward the pooled
  width cell (alpha=20 pseudo-counts) fixes it and alone improves OOT (Sh 2.36 -> 2.96).
- Epistemic divergences (estimation 1/n, ticker-vs-pool shrinkage distance,
  recent-vs-window shift per ticker and market-wide, recent breach-at-strike) are all
  orthogonal to EV but NONE predicts loss. Breach rates at fixed delta are stationary.

Scripts + logs: `research/dkl_2026_09_13/` (untracked). Caches:
`/tmp/gepo_sel_10_30.parquet` (IS), scratchpad `oot_sel_10_30.parquet` (2026 with
quotes, built by `build_oot.py`; SPY csv on the MacBook ends 2026-05-28 so it uses
the parquet's own calendar). Nothing committed, nothing deployed.

## 0.24 Synthetic (fillable) credit — the delta-20 edge does not survive it (2026-09-13)

**Supersedes the conclusion of §0.23.** The user rejected vendor mid AND natural as
credit bases (both are bad EOD quotes). A synthetic credit was built and validated:

- Per chain (Symbol, DataDate, Expiry): robust weighted quadratic smile in ATM-vol
  standardized moneyness (|y| <= 2.5 sigma, bid>0, weights 1/(1+rel width), MAD trims).
  Legs priced by Black-Scholes at the FITTED IV. `research/dkl_2026_09_13/synth_credit.py`.
- Validation on the live IBKR chains (610 snapshots, May-Aug 2026): model credit =
  0.675x the IBKR quoted mid of the ranked picks (IQR 0.60-0.74). The 19 real fills
  were 0.73x quoted, i.e. fills ~1.08x model. **The model IS the fillable price.**
- Stage 2 (`reselect.py`) also picks the short strike by FITTED delta, not vendor delta.

Result at 1.0x model credit, band 0.10-0.30 by fitted delta, top-5/day, thr 0.05:

| | IS 2020-25 | OOT 2026 |
|---|---|---|
| k=0 trades | 1,939 | 296 |
| k=0 final | $10,894 (+9% in 6 yrs) | $10,087 |
| k=0 Sharpe / DD | 0.19 / -31% | 0.30 / -10% |
| 1.1x model (≈ real fills) | Sh 0.89, $16.8k | Sh 1.33 |
| 0.9x model | Sh -0.50, $4.9k | Sh -0.71 |

Every slice of the candidate universe has NEGATIVE mean P&L at fillable credit
(by market risk quintile, IV rank, DTE, direction). Realized loss share 13.1% vs
market-implied 16.1% vs P_emp 12.1%: the market's Q_iv is closer to reality than
the empirical triple on the candidates that get selected (selection picks cells
where P_emp is low, and P_emp is wrong there — excess loss 4-8pp).

DKL at fillable credit: `D(P_emp||Q_iv)` at k>=8 turns the book flat-to-slightly
positive but with 67 trades in 6 years. No form makes a tradeable book. **There is
no DKL to find because there is no edge at a fillable price.** The $38k (§0.23)
and $77k (Mya) books were quote inflation: at k=0, 34% of the vendor-mid picks had
natural credit <= 0, at k=16 66%.

Files: `research/dkl_2026_09_13/{synth_credit,reselect,eval_synth,live_synth}.py`,
logs `eval_synth.log` (vendor-delta strikes), `eval_resel.log` (fitted-delta strikes),
`live_vs_vendor.parquet`, `live_synth.parquet`. Nothing committed or deployed.

## 0.25 Calibrated-market belief (2026-09-13) — what "the market is right" implies

Built after §0.24. Belief P_cal = fitted-surface Q_iv scaled by realized/market
loss and partial ratios learned causally in the market's own buckets (fitted short
delta x DTE x IV-rank tercile, trailing 52 expiries; pooled fallback). It is
calibrated to the third decimal: realized loss share 0.131, P_cal 0.131 (market
0.162, 52:10 P_emp 0.121). `research/dkl_2026_09_13/calib_market.py`, frame `featC.parquet`.

At fillable (model) credit, G on P_cal, top-5/day, k=0:

| fill x model | thr | IS n | IS final | IS Sh | IS DD | OOT n | OOT final | OOT Sh |
|---|---|---|---|---|---|---|---|---|
| 1.0 | 0.05 | 322 | $10,331 | 0.32 | -8.6% | 9 | $10,311 | 3.15 |
| 1.0 | 0.01 | 3,023 | $11,259 | 0.18 | -31.7% | 470 | $13,736 | 3.24 |
| 1.1 (≈ real fills) | 0.01 | 3,023 | $18,359 | 0.94 | -17.0% | 470 | $15,096 | 4.26 |

Year by year at 1.1x / thr 0.01: 2020 Sh 1.19, 2021 1.53, 2022 -0.07, 2023 0.98,
2024 1.74, 2025 0.74, 2026 4.26. The edge is the aggregate VRP on 1-4 DTE
20-delta spreads: ~3pp of loss share, about $3.5/contract at real fills. 2026 has
been unusually good; 2020-25 averages Sh ~0.9 at 1.1x and ~0.2 at 1.0x.

- **thr 0.05 is wrong for a calibrated belief.** It was set on inflated P_emp EVs;
  under P_cal it passes 322 trades in six years. thr 0.01 is the working level.
- **No DKL against P_cal is monotone in both windows.** D(P_cal||Q_iv) unsigned and
  reversed, signed variants, D(P_emp||P_cal), -ln(p_iv): all tested (calib_market.log).
  D(P_emp||P_cal) signed (calibrated market riskier than the name's history) lifts
  IS Sh 0.94 -> 1.10 at k=8 and is flat OOT. With a calibrated belief there is little
  model error left for a divergence to price, which is the correct version of
  §0.22's point 5.
- G on raw Q_iv gives EV~0 everywhere (7 trades). G on 52:10 P_emp gives 1,939
  trades at Sh 0.19 because its EV is invented.

## 0.26 Realized-only belief and divergences (2026-09-13) — user constraint: DKL must use real data

User direction: smile fit is ONLY for the fill credit; G and DKL must use realized data,
no IV, delta, models or calibration. Built (`research/dkl_2026_09_13/real.py`, `real2.py`):

- **P_real** = fraction of the name's own realized DTE-matched moves over the trailing 252
  trading days that would have breached today's exact short / long strikes (+0.5 pseudo-count).
  Calibration: realized loss share 0.131 vs P_real 0.147 (conservative overall, but +6pp
  optimistic on the EV-selected top-5, the usual winner's curse).
- G on P_real at 1.1x model credit (≈ real fills), thr 0.01, k=0: **IS 4,300 trades,
  $26,478, Sh 1.23, DD -21.5%; OOT Sh 1.08.** Beats 52:10 P_emp in-sample (Sh 1.05,
  $22.1k) and the calibrated market (0.94, $18.4k); OOT is weaker than both (1.30, 4.26).
  At 1.0x: IS Sh 0.52, OOT 0.15.

Realized-data divergences tested against P_real, P_emp and their mix (all k sweeps at
fill 1.1 / thr 0.02, quintile loss rates in real.log / real2.log):

| DKL | what it measures | result |
|---|---|---|
| D(P_recent20 ‖ P_year), signed/unsigned | name's realized regime shift at these strikes | loss flat by quintile; Sharpe falls with k |
| D(P_year ‖ P_3yr) | this year vs long-run realized | inert |
| market-wide median of the above | realized vol regime | flat; Sharpe falls |
| D(P_emp ‖ P_real) both directions, signed "tight" | usual 20-delta placement vs today's strikes | loss flat; ±0.05 Sharpe noise |
| signed loss-share gap P_real − P_emp | same, linear | inert |

**Conclusion:** in this product no realized-data divergence carries loss information
beyond a realized-data belief. Every quintile table is flat. The only variable that
predicts loss beyond a name's history is the market's price of risk (fitted IV/delta;
loss 5.9% -> 17.9% across market-risk deciles), which the user has excluded from DKL.
Under that constraint the k=0 book with P_real is the best real-data configuration found,
and its drawdowns (-17% to -33% at Sh ~1) are a correlated-week problem that a per-trade
divergence does not address.

## 0.27 DKL between two MARKET predictions (2026-09-13) — the directional question

User direction: market price of risk IS allowed in DKL; measure DKL between two market
predictions. Regression on all candidates: realized loss share = -0.02 + 0.16*P_real + 0.80*Q_iv.
The market carries ~80% of the outcome information, realized history ~16%.
Scripts `mkt2.py` (today vs yesterday, smile vs flat), `mkt4.py` (this wing vs mirrored wing),
`term.py` (front vs next expiry), `union.py` (max-pessimism reference), `direction.py` log.

**Every market-vs-market divergence is strongly monotone in loss, in the SAFE direction.**
When the market prices this wing richer than its other view (richer than flat vol, richer
than the mirrored wing, richer than yesterday, richer than the next expiry), realized loss
FALLS: smile-vs-flat quintiles IS 16.9% -> 4.7%, OOT 14.8% -> 4.5%; mirrored wing IS
13.3% -> 5.7%. The extra premium is the variance premium; the feared side is the safer side
to sell. exp(-k*D) therefore penalises the edge and Sharpe falls with k for all of them.

**Mirror-signed forms** (fire when this wing is priced CHEAPER than the other market view)
are risk-increasing but tiny (median 0.001-0.005 nats). Best: the max-pessimism reference
D_max = D(Q_ref||Q_smile), Q_ref = the market's most pessimistic view of the spread among
{smile, flat, mirror, yesterday, next expiry}. G on 52:10 P_emp, fill 1.1x model, thr 0.01:
IS Sh 1.27 -> 1.36 (k=4) -> 1.40 (k=8) -> 1.43 (k=16), DD -14.7% -> -12.3%; equal-trade-count
control k=0 gives 1.26, so the in-sample gain is real. OOT: 1.48 -> 1.56 (k=4) -> 1.32 (k=8)
-> 1.21 (k=16). Not robust out of sample beyond k~4. Year-by-year (mirror form): better in
2022/24/25/26, worse in 2020/23.

**Directional question ("does the DKL between the put wing and the call wing tell us
which way the market thinks the stock goes?").** The smile slope c1 (puts richer = market
fears DOWN) does not predict direction: corr(slope, realized return) = -0.04 IS, -0.01 OOT;
P(up) 0.54 vs 0.50 across slope quintiles. It does predict breaches, inversely: the wing the
market pays MORE for breaches LESS (bull_put IS 8.7% -> 7.2%, bear_call 9.1% -> 6.6%; OOT
same shape). The market's directional fear is over-paid on average, not informative about
the sign of the move. This is the 6-year answer to §0.19, which was one year.

**Structural conclusion for GROUND.** At a fillable credit, D(belief || market) and
D(market view A || market view B) both measure how much premium the market adds, and added
premium is edge. A multiplicative discount on that quantity is backwards for a
premium-selling strategy. The only DKLs that increase with realized risk are the
mirror-signed "cheap wing" forms, whose magnitude is too small to reorder a top-5 book
without k in the tens, where the gain is in-sample only.

## 0.28 REWARD form works: GROUND = EV * exp(+k * D_rich) (2026-09-13) — the result of the day

User authorised testing the reward sign. `research/dkl_2026_09_13/reward.py`, log `reward.log`.

    D_rich = D( Q_smile || Q_min ),  Q_min = the market's most OPTIMISTIC view of this spread
             among {flat ATM vol, mirrored wing, yesterday's surface, next-expiry level},
             fired only when this wing is priced RICHER than that view (87.6% of candidates,
             median 0.0099 nats).  Loss falls monotonically with it: IS 13.4% -> 7.7%,
             OOT 12.5% -> 8.6% across quintiles.  It measures how much premium the market
             adds to this wing beyond its own cheapest view = the variance premium.

G on 52:10 P_emp, fill 1.1x model (≈ real fills), thr 0.01, top-5/day:

| k | IS n | IS final | IS Sh | IS DD | IS loss% | OOT n | OOT final | OOT Sh | OOT DD |
|---|---|---|---|---|---|---|---|---|---|
| 0 | 5485 | $25.3k | 1.27 | -14.7% | 12.7 | 596 | $12.3k | 1.48 | -13.5% |
| 4 | 5544 | $30.2k | 1.60 | -15.0% | 12.1 | 597 | $13.1k | 1.96 | -9.9% |
| 8 | 5567 | $31.0k | 1.68 | -13.8% | 11.9 | 597 | $13.5k | 2.18 | -8.8% |
| 16 | 5593 | $30.6k | 1.69 | -11.2% | 11.7 | 600 | $13.3k | 2.09 | -8.2% |
| 32 | 5619 | $31.5k | 1.76 | -10.3% | 11.3 | 600 | $12.6k | 1.61 | -8.1% |
| 64 | 5651 | $33.1k | 1.97 | -10.9% | 10.3 | 600 | $11.6k | 0.96 | -14.8% |

IS Sharpe monotone k=0..64; OOT monotone to k=8, holds to 16, degrades past 32.
**Working range k = 8-16.** Equal-trade-count control (k=0 with threshold lowered to the
same n): IS 1.32 / OOT 1.48 vs reward k=8 1.68 / 2.18 — the gain is the ranking, not the
count. Year by year at k=8 vs k=0: 2020 3.59 vs 3.53, 2021 2.02 vs 2.20, 2022 0.52 vs 0.16,
2023 0.11 vs -0.35, 2024 2.15 vs 1.07, 2025 1.28 vs 0.70, 2026 2.18 vs 1.48. Drawdown
better in 5 of 7 years. At 1.0x fill: IS 0.15 -> 0.62 (k=8), OOT 0.37 -> 0.96.
Component check: smile-vs-flat alone (D_rich_flat) gives IS 1.27 -> 1.61 (k=8), OOT 1.94;
mirror and yesterday each add a little; the skew component carries most of it.
With G on P_real: IS monotone to k=128 (1.23 -> 2.10), OOT noisy (1.0-1.6).

Interpretation: at a fillable price the growth term G finds the candidates with positive
EV under history, and the divergence between the market's own views of the wing measures
how much variance premium the market has loaded onto it. Rewarding that premium is
rewarding the edge; discounting it (the paper's sign) was removing the edge. This is the
sign flip §0.27 predicted. Nothing committed or deployed; canon unchanged.

## 0.29 The simple version (2026-09-13) — wing IV vs ATM IV, reward sign

User asked for something simpler than §0.28. Two Black-Scholes triples for the same strikes:

    Q_wing = N(d2) triple at the smile-fitted IV of each leg     (what the credit is priced at)
    Q_atm  = N(d2) triple at the chain's ATM IV
    D      = D(Q_wing || Q_atm), zero when the wing is priced below ATM
    GROUND = EV * exp(+k * D),  G on the 52:10 empirical belief, k = 8

Fill 1.1x model, thr 0.01, top-5/day (`simple_fit.log`): IS Sh 1.27 -> 1.61 (k=8) -> 1.74
(k=32), monotone; OOT 1.48 -> 1.94 (k=8), 1.72 (k=16), degrades past 24; DD IS -14.7% -> -15.6%
(k=8) / -10.4% (k=32), OOT -13.5% -> -10.4%. Equal-count control k=0: IS 1.32, OOT 1.48.
Year by year at k=8 vs 0: better in 2020-2025 every year except flat 2020; 2026 1.48 -> 1.94.
The RAW vendor-IV version (`simple.py`) is not monotone and 2025 collapses (0.70 -> 0.09):
the strike-level raw IV is the same noisy number that broke the quotes; the smile fit is
required to read the wing IV, exactly as it is for the credit. This is the recommended form.

## 0.30 Delta-band sweep with the full method (2026-09-13) — where the edge is

Method: strikes by FITTED delta, credit = smile-fit model, belief = P_real (name's realized
DTE-matched moves vs the exact strikes, 252d; valid at any delta), reward DKL D(Q_wing||Q_atm)
k=0 and 8, thr 0.01, top-5/day, commission $1.30/spread. `band_sweep.py`, `band2.py`,
`band_checks.py`, logs `band*.log`. Caveat: the pairs file caps OTM at 5%, which biases the
low-delta bands toward low-vol names.

20-60 delta, fill 1.1x, commission in (IS Sh / OOT Sh): 0.15-0.25 0.60/0.17; 0.20-0.30 0.08/-0.24;
0.25-0.35 -0.19/0.29; 0.30-0.40 -0.12/-0.84; 0.35-0.45 -0.05/-0.68; 0.40-0.50 0.40/0.07;
0.45-0.55 1.06/1.03; **0.50-0.60 1.46/2.33**; 0.40-0.60 1.30/2.05. Everything 20-45 delta is
flat-to-negative after commissions. Only the at-the-money band earns.

0.50-0.60 detail (k irrelevant here: the wing-vs-ATM divergence is ~0 at the money):
fill 1.00 IS Sh 0.26 / OOT 0.42; 1.04 0.77 / 1.21; **1.08 (measured fill ratio) 1.25 / 1.97,
$35.9k, DD -18% / -8%**; 1.10 1.46 / 2.35; 1.15 1.94 / 3.24. Break-even fill ~1.03x model.
Year by year at 1.08: 2020 2.22, 2021 1.13, 2022 1.46, 2023 -0.04, 2024 2.29, 2025 1.15, 2026 1.97;
worst weeks -7% to -13%. By DTE: 2-3 best (Sh ~1.0-1.1 IS, 1.5-1.7 OOT), DTE 4 worst OOT.
By direction: bull_put IS 1.17 / OOT 2.18; bear_call 0.12 / -0.16 — the edge is in puts.

10-delta looked best on paper (IS 3.5 / OOT 6.6 with commission) but on real IBKR chains
only 1.2% of 10-delta spreads are fillable at the model credit (median credit $0.10 vs a
$0.12 short-leg bid-ask); user excluded it. Validation on IBKR chains by band: model credit =
IBKR mid to within 2% at every band 15-60 delta (unselected contracts); natural sits far below.

## 0.31 DKL at the money (50-60 delta), fill 1.08x, commission $1.30 (2026-09-13)

`atm_dkl.py`, `atm_cert_checks.log`, `atm_wing.log`. Base k=0: IS Sh 1.25 / $35.9k / DD -18%,
OOT 1.97 / DD -8.1%. Loss quintiles are FLAT (0.42-0.45) for every divergence: at the money
the market and realized history agree, so there is nothing for a divergence to price.

| DKL | discount side (k<0) | reward side (k>0) | verdict |
|---|---|---|---|
| D(P_real ‖ Q_iv), both directions, signed | IS 1.29-1.32, OOT 1.8-2.35 | IS 1.24-1.29, OOT 1.9-2.6 | noise, not monotone |
| today vs yesterday (D_jump, up/dn) | ±0.05 | ±0.05 | inert |
| front vs next expiry (D_term) | ±0.03 (18% coverage) | ±0.03 | inert |
| put wing vs call wing, spread's own strikes (D_mirror) | flat | k=256: IS 1.34, OOT 2.35 | median 1e-5 nats; needs k in the hundreds; sign of loss gradient flips IS vs OOT |
| risk reversal at 1 sigma, signed by side sold (D_rr) | flat | flat | loss gradient IS 0.46->0.40, OOT 0.39->0.48: contradictory |
| -ln p_iv rewarded | -- | k=8: IS 1.41 / $45.9k, OOT 2.34; beats equal-count control (1.24 / 1.94) | corr 0.88 with delta; delta-only control reaches IS 1.46: it is "sell 55-60 delta", not information |

Conclusion: at the money no DKL adds information beyond the delta itself. The put-wing vs
call-wing divergence (user's request) does not predict direction over six years (§0.27) and
at the money its magnitude is too small to reorder a book without k in the hundreds, where the
in-sample and out-of-sample loss gradients disagree. The reward DKL of §0.28-0.29 is a
20-delta result; at 50-delta the selection is G alone.

## 0.32 A DKL that contributes at the money (2026-09-13) — realized-vs-implied exceedance, rewarded

After §0.31 the user required a DKL that contributes in either direction. Tested at 50-60 delta,
fill 1.08x model, commission $1.30, thr 0.01 (`atm2.py`..`atm5.log`): repaired term structure
(still 18% coverage, inert), IV-rank divergence (inert), realized drift vs driftless market
(discount side helps OOT only; IS gain = trade-count reduction per matched control), and:

    p_exceed = trailing-252d frequency (DTE-matched) that |ln move| > 1 ATM-sigma of TODAY's IV
    q_iv     = 2*(1 - N(1)) = 0.317, the lognormal law's exceedance
    D_vrp_neg = D( Bern(p_exceed) || Bern(q_iv) ), fired only when p_exceed > q_iv
               (the name has been moving MORE than its implied vol says)
    GROUND   = EV * exp(+k * D_vrp_neg),  k = 8-12   (REWARD sign)

| k | IS Sh | matched k=0 | IS DD | OOT Sh | matched k=0 | OOT DD |
|---|---|---|---|---|---|---|
| 0 | 1.25 | 1.25 | -18.0% | 1.97 | 1.97 | -8.1% |
| 4 | 1.31 | 1.25 | -18.7% | 2.05 | 2.07 | -8.9% |
| 8 | 1.32 | 1.23 | -16.9% | 2.38 | 1.95 | -6.8% |
| 12 | 1.36 | 1.26 | -16.5% | 2.49 | 2.00 | -6.9% |
| 16 | 1.36 | 1.25 | -17.7% | 2.47 | 1.92 | -7.6% |
| 24 | 1.25 | 1.26 | -19.8% | 2.41 | 1.89 | -8.3% |

Monotone k=0..12 in both windows, beats the equal-count control at k=8-16, fades past 16.
Year deltas at k=12: 2020 -0.03, 2021 +0.47, 2022 -0.26, 2023 +0.25, 2024 +0.37, 2025 -0.12,
2026 +0.52 (4 of 7 up, 2 flat, 2022 down). Holds at every fill 1.00-1.12 (IS +0.10-0.14,
OOT +0.43-0.55). Works through bull puts and DTE 2-3 (DTE3: IS 1.08 -> 1.36, OOT 1.66 -> 2.41).
Loss quintiles are flat (0.41-0.43): it is not a risk measure; it reorders within equal risk.
Mechanism NOT established: the selected book's realized exceedance moves 0.291 -> 0.310 with
IV, credit and name count unchanged. Treat as a modest, plausible-but-unexplained tilt, not
a validated signal. Magnitude ~+0.1 Sharpe IS, +0.5 OOT (2026 is 560 trades).

## 0.33 At-the-money DKL that contributes: sold-tail reward + entropy discount (2026-09-13)

Logs `atm6.log`..`atm9.log`, frame `featATM6.parquet`. 50-60 delta, fill 1.08x model, commission
$1.30, thr 0.01, G on P_real. Names for reference:

- **D_wing** (20-delta result, §0.28/0.29): D(Q_wing || Q_atm), rewarded.
- **D_tail** ("sold-tail divergence", 3-state): order the name's realized DTE-matched moves over
  the trailing 252d into (adverse tail, middle, favourable tail) at ONE IMPLIED SIGMA of today's
  ATM IV, adverse = the direction that hurts the side sold. Q = the normal law (0.159, 0.683, 0.159).
  D_tail = D(P || Q) fired only when the adverse tail is fatter than 0.159 (39% of candidates).
  Rewarded: IS 1.25 -> 1.38 at k=16 (matched control 1.24), OOT 1.97 -> 2.32 (1.93), monotone k=0..16.
- **D_ent** ("entropy discount"): D(Q_bs || uniform) = ln3 - H(Q_bs), the user's uniform-reference
  suggestion. Loss rises 32% -> 51% across its quintiles: a genuine risk measure at the money.
  Discounted. Alone: IS ~= control, OOT +0.5 at k=0.8. The 2-state strike-specific and
  (0.5,0,0.5)-reference variants behave the same way (all in atm6/atm8 logs).

Combined ("tail-entropy GROUND"):

    GROUND = EV * exp( +k1 * D_tail  -  k2 * D_ent )

| k1 | k2 | IS n | IS Sh | ctrl | IS DD | ctrl | OOT Sh | ctrl | OOT DD | years up (of 7) |
|---|---|---|---|---|---|---|---|---|---|---|
| 0 | 0 | 4184 | 1.25 | 1.25 | -18.0 | -18.0 | 1.97 | 1.97 | -8.1 | - |
| 8 | 0.8 | 4258 | 1.36 | 1.23 | -15.7 | -19.0 | 2.63 | 2.05 | -6.3 | 6 |
| 16 | 0.8 | 4379 | 1.47 | 1.24 | -15.4 | -20.0 | 2.42 | 1.95 | -8.4 | 6 |
| 12 | 1.2 | 4261 | 1.42 | 1.23 | -15.2 | -19.0 | 2.59 | 2.05 | -5.9 | 5 |
| 16 | 1.6 | 4234 | 1.47 | 1.24 | -15.3 | -18.3 | 2.00 | 2.07 | -8.4 | 4 |

Recommended cell (8, 0.8) or (16, 0.8). Fill sensitivity at (16, 1.6): IS +0.20-0.25 at every
fill 1.00-1.12; OOT flat there (k2 too high for OOT). Caveats: D_tail's mechanism is not
explained (rewarding a fat adverse tail); the strike-specific 2-state forms do NOT reproduce
it (it is a chain-level property); 2026 is 560 trades. Nothing committed or deployed.

**D_ent ALONE (atm10.log)** — the simplest form, the paper's sign, and risk-monotone:
GROUND = EV * exp(-k * D_ent), D_ent = D(Q_bs || uniform) = ln3 - H(Q_bs), 50-60 delta, fill 1.08, comm $1.30.
k: 0 -> 0.4 -> 0.8 -> 1.2: IS Sh 1.25 -> 1.32 -> 1.35 -> 1.40 (matched ctrl 1.25 / 1.30 / 1.34 / 1.37), IS DD -18.0 -> -14.0;
OOT 1.97 -> 2.26 -> 2.43 -> 2.27 (ctrl ~1.95 / 1.85), OOT DD -8.1 -> -6.6. Year deltas at k=0.8: all seven >= 0
(+0.01, +0.11, +0.28, +0.04, +0.33, +0.05, +0.46); at k=1.2 all seven >= +0.09. Holds at fills 1.00-1.12.
In-sample the gain over the matched control is +0.01 to +0.05 (it works through gating fewer trades);
out of sample +0.4-0.5 over control. Working k = 0.8-1.2. Recommended as the default over the combination:
one parameter, discount sign, loss monotone in D (32% -> 51%).
Note: D_ent IS the paper's risk measure. Mercurio, Wu, Xie, Entropy 2020, 22, 805, Eq. (19):
D_KL(R_Q || U_m) = log(m) - H(R_Q), "the risk of an option strategy portfolio ... relative entropy with
respect to the uniform distribution". With m=3 spread states and Q_bs as the return law it is exactly
what worked at the money. k=1.0 row: IS 1.35 (ctrl 1.36) DD -14.9%, OOT 2.38 (ctrl 1.96) DD -6.6%, 6/7 years >= 0.

## 0.34 Band x k x N sweep for GROUND = EV(P_real) * exp(-k * D_ent) (2026-09-13) — `band_ent.log`

Fill 1.08x model, comm $1.30, thr 0.01, top-5/day, P_real window W=252. IS Sh / OOT Sh:
15-25: 0.32/0.08 (k0) .. 0.59/0.42 (k2.4); 20-45 delta: negative to flat at every k; 40-50: 0.19/-0.24;
45-55: 0.80/0.62 -> 0.79/0.92 (k0.8; ctrl 0.87/0.49); **50-60: 1.25/1.97 -> 1.32/2.26 (k0.4) -> 1.35/2.43
(k0.8; ctrl 1.34/1.95) -> 1.35/2.38 (k1.0) -> 1.40/2.27 (k1.2; ctrl 1.37/1.85) -> 1.35/2.01 (k1.6)**,
IS DD -18.0 -> -14.0; 40-60: 1.04/1.69 -> 1.17/1.55 (k0.8). Working k = 0.8-1.2 on 50-60 only.
N = P_real window W on 50-60 (k=0 -> k=1): W=126 1.17/2.18 -> 1.19/2.42; W=252 1.25/1.97 -> 1.35/2.38;
**W=504 1.37/3.00 -> 1.40/2.90** (n 3470 -> 3223: names need 2 years of history, so 2020-21 coverage drops).
On 40-60, W=252 is best. Recommendation: 50-60 delta, k = 1, W = 252 (or 504 once the history exists), G on P_real.

**NEW CANON (user, 2026-09-13): D_ent.** 50-60 delta by fitted delta, G on P_real (W=252), GROUND = EV*exp(-1.0*D_ent),
D_ent = ln3 - H(Q_bs) (paper Eq. 19), thr 0.01 (0.015 conservative), top-5/day, smile-fit credit, fill 1.08x, comm $1.30.
IS 2020-25: 3,997 trades, $38.3k, Sh 1.35, DD -14.9%. OOT 2026: 544 trades, $14.4k, Sh 2.38, DD -6.6%.
By DTE (`canon_dte.log`), each DTE as its own book k=0 -> k=1: IS DTE1 0.75->0.87, DTE2 0.99->1.09, DTE3 1.08->1.24,
DTE4 0.78->0.85; OOT DTE1 0.88->1.23, DTE2 1.54->1.86, DTE3 1.66->1.66, DTE4 0.39->0.59. k=1 helps every DTE in both
windows. DTE 4 is the weak leg (loss 44-46%, lowest P&L/contract); DTE 1-3 book: IS ~1.35 / OOT 2.6 (DTE1-2) - 2.1 (DTE2-3).
Selected book mix IS: DTE1 17%, DTE2 24%, DTE3 30%, DTE4 29%. Not committed, not deployed; live path still on old canon.
**Execution rule (user, 2026-09-13):** at 50-60 delta the model prices the median spread at credit/width = 0.51
(credit/max-loss 1.0); break-even fill is 0.965x model = credit/width ~0.49. Target credit/width >= 0.50 as the
floor (never less), aim 0.53-0.56 (= 1.04-1.10x model, IS Sh 1.1-1.7). Prefer limit = model_credit x 1.04+ per
spread over a flat ratio, since a 60-delta short carries a higher fair ratio than a 50-delta one. `canon_fill.log`.
**Delta -> target credit/width (`delta_credit_targets.log`, all 50-60 delta candidates 2020-26, smile-fit fair):**
fitted short delta 0.50-0.52: fair 0.437, floor(0.965x) 0.42, min(1.04x) 0.46, good(1.06x) 0.46, obs(1.08x) 0.47, great(1.10x) 0.48;
0.52-0.54: fair 0.477 -> 0.46 / 0.50 / 0.51 / 0.52 / 0.53;  0.54-0.56: fair 0.508 -> 0.49 / 0.53 / 0.54 / 0.55 / 0.56;
0.56-0.58: fair 0.520 -> 0.50 / 0.54 / 0.55 / 0.56 / 0.57;  0.58-0.60: fair 0.526 -> 0.51 / 0.55 / 0.56 / 0.57 / 0.58.
Fair ratio rises ~0.01-0.02 from DTE1 to DTE4 and is flat across the $0.50/$1/$2.50 width rungs. A flat 0.50 floor
rejects fair-or-better 50-52 delta fills and accepts fair 56-60 delta fills: use the per-delta targets.
**Credit-target formula for the tracker (`credit_formula.log`, fit on 43,479 candidates 2020-26, median |err| 0.025):**
  fair(d, DTE) = 0.4022 + 2.3485*(d-0.5) - 12.464*(d-0.5)^2 + 0.0077*(DTE-1)      d = fitted short delta in [0.50, 0.60]
  y  (min credit/width)   = 1.04 * fair      break-even = 0.965 * fair
  (y1, y2) target range   = (1.06 * fair, 1.10 * fair)
e.g. d=0.55 DTE 3: fair 0.504, min 0.524, target (0.534, 0.554). When the smile-fit model credit for the exact spread is
available, use model_credit x {1.04, 1.06, 1.10} instead; the formula is the cross-sectional average.

## 0.35 D_ent canon IMPLEMENTED (2026-09-13) — code, payloads, site

Single source of truth: `ent_canon.py` (smile fit, model credit, P_real, D_ent, Kelly, credit
targets, CANON_LABELS). Used by:
- `report_ent_canon.py` -> `live/data/backtest_equity.json`, `oot_equity.json` (IS 2020-07-14..2025:
  3,997 trades, qty1 $38,347 Sh 1.35 DD -14.9%; OOT 2026-01-02..09-11: 544 trades, qty1 $14,446 Sh 2.34
  DD -6.6%). Reads `research/dkl_2026_09_13/featATM6.parquet` (gitignored, 1.4 GB dir; rebuild from
  the research scripts). IS starts July 2020 because P_real needs 120 sessions of history.
- `live/ranker.py`: fits smiles on the full snapshot, prices candidates, P_real from `live/closes.py`
  (`output/daily_closes.parquet` seeded by `build_daily_closes.py` + last snapshot of each day),
  GROUND = EV*exp(-k*D_ent), thr from config (0.01). Emits model_credit, quoted_credit (IBKR),
  dfit_*, D_ent, credit_targets{min/target_lo/target_hi (credit & c/w), fair_cw, basis}.
  spread_triple / empirical_runner no longer loaded. Verified on live/snapshots/2026-08-19/1531:
  4 qualified picks, e.g. CSCO 112/111 bull_put quoted 0.49 model 0.505 target 0.54-0.56.
- `live/credit_basis.py`: entry credit = actual fill, else 1.08 x model_credit, else 0.80 x mid (pre-canon picks).
- `live/webapp.py`: `_attach_targets` on every pick (history, actuals, live payload); fill graded
  below-min / ok / target / great; History P&L entry credit now via credit_basis.
- Templates: Min / Target column on History and Actuals; live cards show quoted vs model credit and
  targets; chips + subtitles on live/backtest/oot updated; headers renamed real:/bs:/D_ent.
- `config.py`: DELTA 0.55 (0.50-0.60), GROUND_THRESHOLD 0.01. `ground.py`: DKL_K 1.0,
  DKL_REFERENCE "entropy_uniform", PROB_BASIS "realized" (row p_real/q_real/ro_real).
Deploy: code rsync + gunicorn HUP to Mya (webapp.py, templates, ent_canon.py, credit_basis.py, data
JSON). The LIVE tab on Mya shows new fields only once the Mac mini pulls main and its ranker runs.
Not done: fetch_daily_bars does not yet append to daily_closes (snapshots cover it day-to-day).

**Live selection credit = IBKR quoted mid, UNCAPPED (user decision 2026-09-13, "do 2").**
`live_config.LIVE_SELECTION_CREDIT = "quoted"`: the live ranker's Kelly growth runs on the IBKR
combo mid (leg mids without a book), not the smile-fit model credit the backtest selected on.
Stated risk: on the 610 live snapshots the old ranker's picks carried quoted mids 1.3-1.5x the
model with natural ~$0.05, so ranking on the quote favours inflated quotes and diverges from the
backtest. Model credit is still computed on every pick and drives the min / target cells; History
and Actuals still book 1.08x model. Set the flag to "model" to restore backtest-consistent selection.
On 2026-08-19/1531: 9 qualified on the quote vs 4 on the model; ISRG 400/402.5 quoted 1.55 vs model 1.17.
**Execution gate (user 2026-09-13):** a candidate is `qualified` only if GROUND >= 0.01 AND the IBKR credit on the
table (net_credit: combo mid / combo last / leg mids) >= its min credit (1.04 x model). Payload field `above_min`;
live tab shows ✓ / "✗ below min" in the min/target cell; chip "exec gate". On 2026-08-19/1531 it removed RTX
(quoted 1.205 vs min 1.25) and CSCO (0.49 vs 0.53): 7 qualified.
**Live credit priority (user 2026-09-13): combo LAST > combo MID (book narrower than LIVE_COMBO_MAX_WIDTH x width) > leg mids.**
`live/ranker._reprice_on_combos` sets `net_credit` and `credit_source` accordingly; selection, the execution
gate and the live tab all use that credit. A too-wide book with no last now falls back to leg mids instead of
dropping the row. Offline snapshots carry no combo book, so every offline test shows `leg_mid`; the combo
branches only exercise during market hours on the Mac mini.
**Floor restored (user 2026-09-13, "im good with all those"):** `MULT_MIN` back to 1.04x model (break-even is
~1.03x after commission; 1.00x is fair value, now `MULT_WALKAWAY` = the walk-away line, `walkaway_credit` in
targets). The execution gate therefore requires the IBKR credit >= 1.04x model. Actuals page shows median
fill/model and fill/quoted over recorded fills plus the count below min: if fill/quoted runs < ~0.95 for a couple
of weeks, set `LIVE_SELECTION_CREDIT = "model"`.

## 0.47 Option-direction research pass: same-expiry 25-delta skew failed IS validation (2026-09-16)

New isolated research folder: `research/option_direction_2026_09_16/`. No production code changed.

Tested the first/high-priority idea from §0.46 / the ChatGPT comparison:

`skew25 = median IV(puts abs(delta) .20-.30) - median IV(calls abs(delta) .20-.30)` on the same
`ticker, entry_date, expiry_date`; bullish signal = `-skew25` because the earlier 2026-only hint implied richer
put skew was bearish.

Harness: `skew25_validate.py` reads the canon candidate universe
`research/dkl_2026_09_13/featATM6.parquet`, rebuilds current canon P_real/GROUND using `ent_canon.py`
and `output/name_gaps_backtest.parquet`, builds `skew25_by_chain.parquet` from the vendor yearly option files,
then reports:
- pooled and within-date Spearman of bullish_skew25 vs expiry return;
- within-date shuffle null, preserving each trading day's cross-section;
- date-quintile full-win tables;
- selected-book overlays: base canon, +5% skew tilt, +10% skew tilt, bottom-20% skew veto. Skew sign is fitted
  walk-forward using prior years only.

Result: do **not** integrate this raw same-expiry skew signal. 2026 alone beats the date-shuffle null
(daily Spearman 0.0704 vs null p95 0.0295), but 2020-2025 all fail the null and are mostly negative/flat:
2020 -0.0198, 2021 -0.0072, 2022 -0.0050, 2023 -0.0054, 2024 0.0008, 2025 -0.0173.
Selected-book overlays also underperform the current canon:
base 5,154 trades / $37,047 / full-win 43.89% / profitable 53.10%;
+5% tilt $35,756 / 43.83% / 53.07%;
+10% tilt $36,122 / 43.83% / 53.14%;
bottom-20% veto $30,741 / 43.38% / 52.71%.

Artifacts:
- `skew25_validate.txt`: full report.
- `skew25_years.csv`: year-level rank/null inputs.
- `skew25_buckets.csv`: quintile table.
- `skew25_by_chain.parquet`: derived 643 KB cache from vendor option files; safe to regenerate with
  `python3 research/option_direction_2026_09_16/skew25_validate.py --force-skew`.

Next best tests: same-strike call-put IV residual, then new-OI repricing. Avoid spending more time on raw
same-expiry 25-delta put-call skew unless a materially different causal definition is proposed.

## 0.48 Option-direction bakeoff: 10-signal suite coded and run (2026-09-16)

User pushed back correctly: testing only one idea was not enough. Added
`research/option_direction_2026_09_16/direction_signal_suite.py` and ran the broader bakeoff.

What the suite does:
- builds `chain_direction_features.parquet` from the yearly vendor option files, restricted to candidate names/dates
  but including all expiries up to DTE 45 so term-structure signals have a next expiry;
- recomputes the current canon P_real/GROUND from `ent_canon.py`;
- evaluates historically available option-market signals with walk-forward sign fitting;
- compares each signal via pooled rank correlation to expiry return plus two overlays on the current book:
  `tilt10` and `veto_bottom20`.

Backtested signals in this pass:
1. 25d put-call skew level.
2. 25d skew change.
3. Same-strike near-ATM put-call IV gap.
4. Dealer GEX proxy from signed gamma*OI.
5. Max-OI pin direction.
6. Call-minus-put OI pressure change.
7. Front ATM IV vs next-expiry ATM IV.
8. Front skew vs next-expiry skew.
9. Canon smile slope `c1`.
10. ATM IV change residual after same-day stock return.

Not truly backtestable from current local history:
- earnings event skew: local `data/earnings_calendar.csv` only covers 2026;
- live option volume flow and live quote-size imbalance: no historical option volume or size fields in the backtest.

Headline result with the current own-gap drift ON: the only overlay that beat the base selected book was
**dealer_gex / veto_bottom20**.
Base canon: 5,154 trades / $37,047 / full-win 43.89% / profitable 53.10%.
Dealer GEX veto bottom 20%: 4,749 trades / $40,444 / full-win 44.41% / profitable 53.65%, changing 23.6% of base selections.

Own-gap OFF ablation added immediately after user asked. Run:
`python3 research/option_direction_2026_09_16/direction_signal_suite.py --no-gap`.
No-gap base canon: 5,146 trades / $35,027 / full-win 43.55% / profitable 53.07%.
No-gap dealer GEX veto bottom 20%: 4,728 trades / $34,870 / full-win 43.97% / profitable 53.17%, changing 23.0%.
No-gap dealer GEX tilt10: 5,146 trades / $36,488 / full-win 43.68% / profitable 53.26%, changing 1.6%.
Conclusion: the big GEX-veto improvement is **gap-dependent** and should not be treated as a standalone options edge yet.
If continuing, the second pass must compare gap-on/off jointly with a date-shuffle null, per-year deltas, and
decomposition into unchanged/side-flip/replacement.

Other notes:
- `smile_slope` had the best mean walk-forward Spearman (0.0467 across 2021-2026), but both overlays lost money vs base;
  the strong rank correlation did not convert to selection improvement under the simple tilt/veto tested here.
- `same_strike_cp_iv` was positive in rank diagnostics, but overlays lost vs base; possible second-pass with a better integration rule, not production.
- `skew_term_structure` veto was near-flat/slightly positive ($37,099 vs $37,047) but tiny edge and low coverage.
- raw `skew25_level` remained bad, consistent with §0.47.

Artifacts:
- `direction_signal_suite.py`: suite.
- `direction_signal_suite.txt`: full report.
- `direction_signal_summary.csv`: year-level diagnostics.
- `direction_signal_books.csv`: selected-book overlays.
- `direction_signal_suite_nogap.txt`, `direction_signal_summary_nogap.csv`, `direction_signal_books_nogap.csv`:
  same suite with own-gap drift disabled.
- `chain_direction_features.parquet`: derived cache, safe to regenerate with
  `python3 research/option_direction_2026_09_16/direction_signal_suite.py --force-chain`.

Do not integrate anything yet.

## 0.49 Direction stacking pass: combinations are stronger than single signals (2026-09-16)

Added `research/option_direction_2026_09_16/direction_signal_stack.py`. It imports the §0.48 feature builders and
tests walk-forward stacks without touching production:
- `stack_equal_*`: equal rank-average of signed signals;
- `stack_weighted_*`: prior-year correlation-weighted rank blend;
- veto combos: `veto_gex_cp_iv`, `veto_gex_ivresid`, `veto_gex_skewterm`, `veto_top4_bad`.

Signs/weights are fitted only on years before the scored year. 2020 has no prior years, so stack components are neutral.

Gap ON results (`direction_signal_stack.txt`):
- Base canon: 5,154 trades / $37,047 / full-win 43.89% / profitable 53.10%.
- Best stack: `veto_gex_cp_iv` bottom-20%:
  4,587 trades / $41,454 / full-win 46.02% / profitable 54.33%, changing 29.8% of base selections.
- Next: `veto_gex_skewterm` bottom-20%:
  4,753 trades / $41,224 / full-win 44.54% / profitable 53.94%, changing 22.9%.
- Per-year for best stack is uneven: strong 2021/2024/2025, weak-but-positive 2022/2023, 2026 $2,953 vs base 2026 $4,591
from the gap-on single-signal suite. So this is not a clean live-ready rule yet.

Gap OFF results (`direction_signal_stack_nogap.txt`):
- Base no-gap: 5,146 trades / $35,027 / full-win 43.55% / profitable 53.07%.
- Best stack: `veto_top4_bad` bottom-30%:
  4,193 trades / $38,527 / full-win 46.12% / profitable 54.26%, changing 38.8% of base selections.
- Next: `veto_gex_cp_iv` bottom-20%:
  4,563 trades / $38,261 / full-win 45.89% / profitable 54.11%, changing 30.3%.
- No-gap stack also has uneven year profile: strong 2021/2024/2025/2026, slightly negative 2022/2023. This suggests
the stack may be selecting a regime/volatility state, not a stable daily directional edge.

Bottom line: stacking produced the first results with the desired +1-2 point full-win lift, and it survives no-gap in
some forms. But the improvement comes from large vetoes/replacements, not small tilts. Next pass must do:
date-shuffle null for the stack scores, unchanged/side-flip/replacement decomposition, per-year trade counts and PnL,
and a focused search over veto thresholds with search-adjusted null.

Artifacts:
- `direction_signal_stack.py`
- `direction_signal_stack.txt`, `direction_signal_stack_books.csv`, `direction_signal_stack_years.csv`
- `direction_signal_stack_nogap.txt`, `direction_signal_stack_books_nogap.csv`, `direction_signal_stack_years_nogap.csv`

## 0.50 New option-market idea: same-strike parity-implied forward pressure (2026-09-16)

User wanted a genuinely different options-data idea, not more skew/OI threshold grinding. Added
`research/option_direction_2026_09_16/parity_forward_signal.py`.

Idea: pair calls and puts at the same strike and compute the option-implied forward:

`F_impl(K) = K + call_mid(K) - put_mid(K)` over near-ATM strikes.

Features:
- `parity_fwd_z`: median `(F_impl / spot - 1) / expected_move`;
- `parity_fwd_slope`: slope of implied forward across moneyness;
- `parity_fwd_iqr`: dispersion of implied forward across same-strike pairs;
- `parity_balance`: median `(call_mid - put_mid) / (call_mid + put_mid)`;
- `parity_oi_balance`: same-strike call-vs-put OI balance;
- daily changes for the main parity features.

Standalone parity result: `parity_balance` veto 5% was excellent aggregate
(4,955 trades / $40,908 / full-win 45.23% / profitable 54.25%), but failed 2026:
2020-25 $38,080 vs base $32,456; 2026 $2,828 vs base $4,591. Not acceptable alone.

Combined parity + existing GEX/skew-term targeted search:
`min(veto_gex_skewterm_rank, parity_balance_rank)` with bottom-15% veto is the best new both-period candidate.
Saved as `min_gex_skewterm_parity_balance_veto15_metrics.csv`.

Metrics, gap-on:
- 2020-25 base: 4,563 trades / $32,643 / full-win 44.09% / profitable 53.19% / weekly Sh 1.52 / Calmar 2.36 / yield 10.15%.
- 2020-25 combo: 3,891 trades / $33,462 / full-win 45.70% / profitable 54.38% / weekly Sh 1.45 / Calmar 1.78 / yield 10.75%.
- 2026 base: 593 trades / $4,591 / full-win 42.50% / profitable 52.61% / weekly Sh 2.97 / Calmar 18.28 / yield 10.44%.
- 2026 combo: 544 trades / $5,030 / full-win 44.49% / profitable 53.49% / weekly Sh 3.22 / Calmar 18.89 / yield 11.75%.

Read: this is better on the user's core ask (direction/win-rate in both IS and 2026) and gives a meaningful 2026
improvement. It still worsens 2020-25 Sharpe/Calmar because it is a large veto and concentrates risk. Next pass:
date-shuffle/search-adjusted null for the combined score, and decomposition of removed/replaced trades.

Artifacts:
- `parity_forward_features.parquet`
- `parity_forward_signal.py`
- `parity_forward_signal.txt`, `parity_forward_summary.csv`, `parity_forward_books.csv`
- `parity_top_split_metrics.csv`
- `parity_gex_targeted_joint.csv`
- `min_gex_skewterm_parity_balance_veto15_metrics.csv`

## 0.51 Strike-local / side-conditional option-book direction pass (2026-09-16)

User pushed for a stronger options-market directional signal that improves both 2020-25 and 2026, including Sharpe,
yield, Calmar, and win rate. New pass moved from chain-level summaries to strike-local structure at the exact
candidate strikes and corrected the signal orientation by trade side:
- bull puts want bullish/support signals high;
- bear calls want bearish/resistance signals high.

Added `research/option_direction_2026_09_16/strike_local_oi_gex_search.py` and built
`strike_local_oi_gex_features.parquet` from the full yearly option-chain parquet files. Features include local OI
walls, same-strike OI put/call balance, local GEX/DEX, risk-vs-safe-side OI, and side-aligned parity balance.

Important implementation note: the useful rules are side-conditional. Applying the same option-book threshold to
both bull puts and bear calls is weaker. Best balanced current candidate:

`bear_call_min_parity_gex_pct_veto08`

Definition:
- For each candidate/year, compute walk-forward-signed ranks:
  - `r_side_parity_balance_wf`: side-aligned same-strike parity premium balance, with sign fit only on prior years.
  - `r_veto_gex_skewterm_wf`: prior-year-signed GEX/skew-term veto score.
- Composite raw score = `min(r_side_parity_balance_wf, r_veto_gex_skewterm_wf)`.
- Convert that composite to a within-year percentile.
- For **bear calls only**, veto candidates below the 8th percentile. Bull puts are untouched.
- Selection remains canon `GROUND >= THR`, top 5/day.

Exact full metrics saved in `bear_call_min_parity_gex_pct_veto08_full_metrics.csv`.

Split metrics:
- 2020-25 base: 4,563 trades / $32,643 PnL / full-win 44.09% / profitable 53.19% / weekly Sh 1.532 /
  Calmar 3.193 / yield 10.15%.
- 2020-25 overlay: 4,503 trades / $33,688 PnL / full-win 44.37% / profitable 53.34% / weekly Sh 1.549 /
  Calmar 3.257 / yield 10.45%.
- 2026 base: 593 trades / $4,591 PnL / full-win 42.50% / profitable 52.61% / weekly Sh 3.191 /
  Calmar 19.35 / yield 10.44%.
- 2026 overlay: 588 trades / $5,554 PnL / full-win 43.20% / profitable 53.40% / weekly Sh 3.450 /
  Calmar 23.47 / yield 12.71%.
- Overall: $39,242 vs $37,234, full-win 44.23% vs 43.91%, profitable 53.35% vs 53.12%,
  weekly Sh 1.668 vs 1.622, yield 10.72% vs 10.18%.

This is the first found rule in this direction pass that improves **both** 2020-25 and 2026 across PnL, full-win,
profitable rate, yield, weekly Sharpe, and Calmar. It is still modest in 2020-25 (+$1,045 / +0.28 full-win pt), so
do not overstate it as proven alpha. It is a credible candidate for a date-shuffle/search-adjusted null and a live
paper run.

Related search artifacts:
- `side_aligned_direction_search_gap_on.csv`
- `strike_local_narrow_search.csv`, `strike_local_narrow_best_metrics.csv`
- `side_conditional_search.csv`, `side_conditional_best_metrics.csv`
- `dual_side_conditional_grid.csv`, `dual_side_conditional_best_metrics.csv`
- `bear_call_min_parity_gex_pct_veto08_full_metrics.csv`

## 0.52 Add a bull-put veto to the bear-call candidate (2026-09-16)

Tested the current `bear_call_min_parity_gex_pct_veto08` with a side-conditional
bull-put veto added. The bull-put score was the strike-local side-aligned
combination of parity balance and local GEX; the bear-call rule was held fixed
at the 8th-percentile veto.

Result: no bull-put cutoff tested improved the bear-only candidate. The least
damaging cutoff was 1%, but it still reduced PnL in both periods. A 35% cutoff
raised 2026 PnL but damaged 2020-25 PnL and all win-rate metrics. Therefore the
best current implementation remains bear-call-only; adding a symmetric bull-put
veto does not balance the book profitably in this sample.

Representative combined results:
- bear-only: 2020-25 $33,688 / 44.37% full-win; 2026 $5,554 / 43.20% full-win.
- bull veto 1% + bear veto 8%: 2020-25 $33,421 / 44.40% full-win; 2026 $4,995 / 42.81% full-win.
- bull veto 35% + bear veto 8%: 2020-25 $32,450 / 44.32% full-win; 2026 $6,036 / 44.29% full-win.

The full threshold grid is in `dual_side_conditional_grid.csv`.

## 0.53 Independent side-threshold portfolio (2026-09-16)

Tested abandoning the global top-five cap. The selector now admits up to five
bull puts and up to five bear calls independently each day, subject to:
- `GROUND >= 0.005`;
- a daily cross-sectional option score threshold;
- separate side caps of five.

The score is the average daily percentile of walk-forward-signed GEX and
same-strike call/put signals for bull puts; bear calls use its complement.
This avoids year-level percentile look-ahead.

The coarse 7 x 11 x 11 sweep found no pair improving all six target metrics in
both 2020-25 and 2026. The most balanced near-miss was bull cutoff 5%, bear
cutoff 25%: 2020-25 PnL $36,557 / full-win 44.44% / weekly Sharpe 2.516;
2026 PnL $3,425 / full-win 42.00% / weekly Sharpe 3.209. It improves 2026
risk-adjusted performance but is not a replacement for the current bear-call
veto candidate because 2026 PnL and 2020-25 Sharpe are weaker in this test.

Reproducible code and output:
- `threshold_side_portfolio_search.py`
- `threshold_side_portfolio_search.csv`
