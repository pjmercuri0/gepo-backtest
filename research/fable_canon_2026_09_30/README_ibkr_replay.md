# IBKR fair-replay candidate frame (mini, 2026-09-30)

`ibkr_replay_candidates_1530_1545.csv`: every candidate the CURRENT canon builds when each archived
15:30/15:45 IBKR snapshot (live/snapshots/<day>/<HHMM>.parquet, 2026-08-20..09-24) is re-ranked with
`live.ranker.rank_snapshot` and `LIVE_COMBO_ENABLED = False` (quotes = snapshot leg mids; no IB calls).
Columns are the ranker's: quoted `net_credit`, smile-fit `model_credit`, `IV`, P_real triple, `G`, `EV`,
`D_ent`, `GROUND` (k as in ground.DKL_K at run time = 4), `G24` = EV*exp(-24 D_ent), `settle` = expiry close.
Bull puts only were built (SPY above its 100d SMA all window). Selection used for the "fair replay" numbers in
handoff §0.68/§0.69: model_cw >= 0.45, net_credit >= model_credit, pooled top 6 per scan by G24, 15:30 then
15:45 top-up to 6, booked 1.04 x model. Join with the vendor frame on (day, ticker) to see where the two
sources disagree (strikes, model credit, IV, quote, floor/gate pass, GROUND rank).

## `ibkr_late_day_chains_2026-08-20_to_09-30.parquet` (mini, 2026-09-30)

The latest full 15:xx IBKR snapshot per day, for smile fits: 15:31 scans for Aug 20 - Sep 1 (hourly :01/:31 cron
then), 15:45 scans from Sep 2. 36,388 rows, 27 days, 96 names; columns are the fetcher's (Symbol, DataDate,
ExpirationDate, StrikePrice, conId, PutCall, Bid/Ask/Last, Theta, DTE, AbsDelta, UnderlyingPrice, ImpliedVolatility,
OpenInterest, Volume, Bid/AskSize) plus `day` and `snapshot_hhmm`. Market data type 1 (live quotes, not delayed).
Missing: Aug 21, Aug 28 (no 15:xx scans archived), Sep 7 (holiday). The 16:00 scans are post-close and sparse
(150-790 rows), not usable. True IBKR end-of-day chains for EXPIRED weeklies cannot be re-fetched from the API, so
this is the closest IBKR "EOD" that exists. Feed it to `ent_canon.fit_smiles` per (Symbol, DataDate, ExpirationDate).
