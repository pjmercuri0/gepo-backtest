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
