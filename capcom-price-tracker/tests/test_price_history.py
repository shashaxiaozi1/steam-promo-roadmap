import csv
import datetime as dt
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).parents[1]))
import collector as c
from price_history import JST, merge_daily, reconstruct


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.item = dict(publisher="SEGA", appid="1", game_name="One", package_id="10", tracked_edition="Base")

    def tearDown(self):
        self.tmp.cleanup()

    def history(self, rows):
        path = self.root / "source.csv"
        c.atomic_csv(path, ["DateTime", "Final price", "Historical Low"],
                     [dict(zip(["DateTime", "Final price", "Historical Low"], r)) for r in rows])
        return reconstruct(path, self.item, "10.00")

    def test_jst_midnight_exclusive_end_and_blank_chart_endpoint(self):
        days, intervals, meta = self.history([
            ("2025-09-10 15:00:03", "9.00", "9.00"),
            ("2025-09-11 15:00:00", "10.00", "9.00"),
            ("2025-09-12 15:00:00", "10.00", "9.00"),
            ("2025-09-12 15:00:00", "", "9.00"),
        ])
        self.assertEqual([x["date_jst"] for x in days], ["2025-09-11", "2025-09-12"])
        self.assertEqual([x["is_on_sale_any"] for x in days], [1, 0])
        self.assertEqual([x["max_discount_percent"] for x in days], [10, 0])
        self.assertEqual(intervals[0]["start_jst"], "2025-09-11T00:00:03+09:00")
        self.assertEqual(meta["blank_price_rows"], 1)
        self.assertTrue(all(x["sample_count"] == 0 for x in days))

    def test_no_carry_before_start_after_end_or_across_unknown_price(self):
        days, _, _ = self.history([
            ("2025-09-10 15:00:00", "10.00", "9.00"),
            ("2025-09-11 12:00:00", "", "9.00"),
            ("2025-09-13 15:00:00", "9.00", "9.00"),
            ("2025-09-14 15:00:00", "10.00", "9.00"),
        ])
        self.assertEqual([x["date_jst"] for x in days], ["2025-09-14"])

    def test_invalid_prices_conflicting_timestamps_and_new_baseline_require_review(self):
        for rows in ([('2025-09-10 15:00:00', '9.00', '9.00'), ('2025-09-10 15:00:00', '8.00', '8.00')],
                     [('2025-09-10 15:00:00', '11.00', '9.00'), ('2025-09-11 15:00:00', '10.00', '9.00')],
                     [('2025-09-10 15:00:00', '9.876', '9.00'), ('2025-09-11 15:00:00', '10.00', '9.00')]):
            with self.assertRaises(ValueError):
                self.history(rows)

    def test_next_collection_preserves_import_does_not_invent_publisher_denominator(self):
        days, _, _ = self.history([
            ("2025-09-10 15:00:00", "9.00", "9.00"),
            ("2025-09-11 15:00:00", "10.00", "9.00"),
            ("2025-09-12 15:00:00", "10.00", "9.00"),
        ])
        c.atomic_csv(self.root / "history/1/daily_games.csv", list(days[0]), days)

        def fetch(pid):
            return dict(observed_at_utc="2026-11-11T01:00:00+00:00", body={"10": dict(success=True, data=dict(
                apps=[dict(id=1)], price=dict(currency="USD", initial=1000, final=1000, discount_percent=0)))})
        c.collect([self.item], self.root, fetch, "20261111T010000Z")
        games = c.read_csv(self.root / "powerbi/daily_games.csv")
        pubs = c.read_csv(self.root / "powerbi/daily_publishers.csv")
        self.assertEqual(len(games), 3)
        self.assertEqual([(p["date_jst"], p["games_monitored"]) for p in pubs], [("2026-11-11", "1")])
        self.assertEqual(len(c.read_csv(self.root / "powerbi/snapshots.csv")), 1)

    def test_overlap_keeps_live_sample_count_merges_sale_evidence_and_rejects_other_edition(self):
        days, _, _ = self.history([
            ("2026-11-10 15:00:00", "9.00", "9.00"),
            ("2026-11-11 12:00:00", "10.00", "9.00"),
            ("2026-11-11 15:00:00", "10.00", "9.00"),
        ])
        live = dict(days[0], sample_count=2, is_on_sale_any=0, max_discount_percent=0, lowest_sale_price_usd="10.00")
        merged = merge_daily([live], days)
        self.assertEqual((merged[0]["sample_count"], merged[0]["is_on_sale_any"], merged[0]["max_discount_percent"]), (2, 1, 10))
        with self.assertRaises(ValueError):
            merge_daily([dict(live, package_id="20")], days)

    def test_retirement_filter_also_applies_to_imports(self):
        days, _, _ = self.history([
            ("2025-09-10 15:00:00", "9.00", "9.00"),
            ("2025-09-12 15:00:00", "10.00", "9.00"),
        ])
        c.atomic_csv(self.root / "history/1/daily_games.csv", list(days[0]), days)
        c.write_json(c.lifecycle_path(self.root, "SEGA"), {"retired": {"1": {
            "catalog_entry": self.item, "effective_date_jst": "2025-09-12"}}})
        games, pubs = c.build_reports([], self.root)
        self.assertEqual([x["date_jst"] for x in games], ["2025-09-11"])
        self.assertEqual(pubs, [])


if __name__ == "__main__":
    unittest.main()
