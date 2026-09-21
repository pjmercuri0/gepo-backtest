"""Every number the site shows must reconcile against its source.

These are the checks that were run BY HAND on 2026-09-21 after the user found,
in one afternoon: Actuals and History disagreeing on spot (three different
sources), a card header disagreeing with the rows under it, a settled row
showing the canon P&L instead of the user's fill, and marks collapsing to
intrinsic because a module was missing on the server.

None of that should ever reach him again. Run on every scan via cron_health.
"""
import unittest

import spreads
from live.webapp import (_actuals_rows, _actuals_row_pnl, _actuals_weeks,
                         _frozen_history, _live_status, app)
from live.credit_basis import entry_credit


class DisplayConsistencyTests(unittest.TestCase):

    def test_open_actuals_pnl_is_fill_minus_mark(self):
        for r in _actuals_rows():
            p = r.get("pick") or {}
            if r.get("outcome_row"):
                continue
            lt = r.get("last_track") or {}
            mark, fill = lt.get("current_mark"), p.get("actual_credit")
            got = _actuals_row_pnl(r)[0]
            if mark is None or fill is None or got is None:
                continue
            want = round((float(fill) - float(mark)) * 100, 2)
            self.assertAlmostEqual(got, want, places=1,
                                   msg=f"{p.get('ticker')}: open P&L != (fill - mark)")

    def test_settled_actuals_pnl_uses_the_users_fill(self):
        for r in _actuals_rows():
            p, o = r.get("pick") or {}, r.get("outcome_row") or {}
            fill, spot = p.get("actual_credit"), o.get("underlying_price")
            if not o.get("result") or fill is None or spot is None:
                continue
            ss, ls = float(p["short_strike"]), float(p["long_strike"])
            aml = p.get("actual_max_loss")
            aml = float(aml) if aml is not None else abs(ss - ls) - float(fill)
            want = round(spreads.settle_pnl(float(spot), ss, ls, float(fill), aml,
                                            p["spread_type"]) * 100, 2)
            self.assertAlmostEqual(_actuals_row_pnl(r)[0], want, places=1,
                                   msg=f"{p.get('ticker')}: settled P&L not at the user's fill")

    def test_card_header_equals_sum_of_its_rows(self):
        rows = _actuals_rows()
        for wk in _actuals_weeks(rows):
            s = 0.0
            for r in wk["rows"]:
                v = _actuals_row_pnl(r)[0]
                if v is not None:
                    s += v * int((r.get("pick") or {}).get("suggested_qty") or 1)
            self.assertAlmostEqual(wk["total"], s, places=1,
                                   msg=f"{wk['expiry']}: header != sum of rows")

    def test_marks_lie_inside_zero_and_width(self):
        for r in _actuals_rows():
            p, lt = r.get("pick") or {}, r.get("last_track") or {}
            mark = lt.get("current_mark")
            if mark is None or r.get("outcome_row"):
                continue
            w = abs(float(p["short_strike"]) - float(p["long_strike"]))
            self.assertGreaterEqual(float(mark), -1e-9, f"{p.get('ticker')}: mark < 0")
            self.assertLessEqual(float(mark), w + 1e-9, f"{p.get('ticker')}: mark > width")

    def test_status_badge_matches_spot_versus_strikes(self):
        for r in _actuals_rows():
            p, lt = r.get("pick") or {}, r.get("last_track") or {}
            spot = lt.get("underlying_price")
            if spot is None or r.get("outcome_row"):
                continue
            want = _live_status(p.get("spread_type"), spot,
                                p.get("short_strike"), p.get("long_strike"))
            self.assertEqual(lt.get("live_status"), want,
                             f"{p.get('ticker')}: badge disagrees with spot vs strikes")

    def test_open_history_pnl_is_canon_credit_minus_mark(self):
        for e in _frozen_history(limit=3):
            settled = set(((e.get("outcome") or {}).get("results") or {}).keys())
            for p in e.get("top_picks") or []:
                tk = p.get("ticker")
                arr = (e.get("tracking") or {}).get(tk) or []
                if p.get("pnl") is not None or tk in settled or not arr:
                    continue
                lt = arr[-1]
                mark, got = lt.get("current_mark"), lt.get("unrealized_pnl_per_contract")
                if mark is None or got is None:
                    continue
                want = round((entry_credit(p) - float(mark)) * 100, 2)
                self.assertAlmostEqual(got, want, delta=1.0,
                                       msg=f"{tk}: History P&L != (canon credit - mark)")

    def test_rendered_actuals_cells_match_the_source(self):
        """Parse the HTML, not the helper.

        The helper can be right while the template renders something else: MA
        570/567.5 showed $29.50 in its row (the canon P&L) while the card header
        said $17.00 (the user's fill).

        Rows are matched on the trade id the remove button already carries.
        Matching on ticker+strikes is NOT enough -- CSCO 111/110 exists on both
        the Sep 18 and Sep 25 expiries, and JNJ 270/267.5 and 272.5/270 share a
        leg. Both of those produced false failures while writing this.
        """
        import re
        html = app.test_client().get("/actuals").data.decode()
        by_id = {}
        for row in re.findall(r"<tr[^>]*>.*?</tr>", html, re.S):
            m = re.search(r'data-id="([^"]+)"', row)
            if not m:
                continue
            by_id[m.group(1)] = [
                re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", c)).strip()
                for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]

        checked = 0
        for r in _actuals_rows():
            cells = by_id.get(r.get("id"))
            if not cells:
                continue
            m = re.fullmatch(r"\$([+-]?[0-9,]+)", cells[-1])
            want = _actuals_row_pnl(r)[0]
            if not m or want is None:
                continue
            shown = float(m.group(1).replace(",", ""))
            checked += 1
            self.assertAlmostEqual(
                shown, round(want), delta=1.0,
                msg=f"{r.get('id')}: row shows {shown} but source says {want}")
        self.assertGreater(checked, 0, "no rendered rows were checked; test proved nothing")

    def test_every_page_renders(self):
        c = app.test_client()
        for path in ("/", "/history", "/actuals", "/snapshots", "/backtest", "/oot"):
            self.assertEqual(c.get(path).status_code, 200, f"{path} did not render")

    def test_tabs_agree_on_spot_for_the_same_ticker(self):
        """The bug that cost the most time: three sources for one number."""
        import json, re
        from pathlib import Path
        import live.webapp as W
        from live import live_config
        snap = W._read_json(Path(live_config.RANKED_DIR) / "combo_stream.json") or {}
        orig = W._read_json
        W._read_json = lambda p: snap if str(p).endswith("combo_stream.json") else orig(p)
        try:
            c = W.app.test_client()
            pages = {p: c.get(p).data.decode() for p in ("/history", "/actuals")}
        finally:
            W._read_json = orig

        def price_for(html, tk, ss, ls):
            """Spot on the row for THIS spread -- matched on strikes, not just the
            ticker. Matching by ticker alone compares different positions: on
            2026-09-21 History carried a SETTLED JNJ 270/267.5 from the 09-17
            entry (frozen at its 269.99 expiry close) while Actuals held an OPEN
            JNJ 272.5/270 for Sep 25 at the live 269.63. Both correct."""
            # BOTH legs must match, as a pair. Intersecting on either strike is
            # not enough: JNJ 270/267.5 and JNJ 272.5/270 share the 270 leg and
            # are different spreads.
            i = html.find(">" + tk)
            while i != -1:
                row = html[html.rfind("<tr", 0, i):html.find("</tr>", i)]
                pairs = re.findall(r"\$([0-9]+(?:\.[0-9]+)?) ?/ ?\$([0-9]+(?:\.[0-9]+)?)", row)
                if any(abs(float(a) - ss) < 1e-6 and abs(float(b) - ls) < 1e-6
                       for a, b in pairs):
                    m = re.findall(r"\$([0-9]{1,4}\.[0-9]{2})", row)
                    if m:
                        return m[0]
                i = html.find(">" + tk, i + 1)
            return None

        checked = 0
        for r in _actuals_rows():
            pk = r.get("pick") or {}
            if r.get("outcome_row"):
                continue
            tk = pk.get("ticker")
            try:
                ss, ls = float(pk["short_strike"]), float(pk["long_strike"])
            except (KeyError, TypeError, ValueError):
                continue
            vals = {p: price_for(h, tk, ss, ls) for p, h in pages.items()}
            vals = {k: v for k, v in vals.items() if v}
            if len(vals) < 2:
                continue          # the spread only appears on one tab
            checked += 1
            self.assertLessEqual(len(set(vals.values())), 1,
                                 f"{tk} {ss:g}/{ls:g}: tabs disagree on spot -> {vals}")
        self.assertGreater(checked, 0, "no spread appeared on both tabs; test proved nothing")


if __name__ == "__main__":
    unittest.main()
