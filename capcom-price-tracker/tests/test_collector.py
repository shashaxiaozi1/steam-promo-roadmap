import csv
import importlib.util
from pathlib import Path
import tempfile
import unittest


spec = importlib.util.spec_from_file_location("collector", Path(__file__).parents[1] / "collector.py")
collector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(collector)


def response(pid, appid, observed, discount=50, final=500):
    return {
        "package_id": pid, "observed_at_utc": observed,
        "body": {str(pid): {"success": True, "data": {
            "apps": [{"id": appid}], "price": {"currency": "USD", "initial": 1000,
                                              "final": final, "discount_percent": discount},
        }}},
    }


class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.catalog = [
            {"publisher": "CAPCOM", "appid": "1", "game_name": "One", "package_id": "10", "tracked_edition": "Base"},
            {"publisher": "CAPCOM", "appid": "2", "game_name": "Two", "package_id": "20", "tracked_edition": "Deluxe"},
        ]
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_two_runs_rebuild_daily_any_sale_and_keep_snapshots(self):
        morning = "2026-10-30T00:00:00+00:00"
        evening = "2026-10-30T12:00:00+00:00"

        def fetch1(pid):
            return response(pid, pid // 10, morning, 0, 1000)

        def fetch2(pid):
            return response(pid, pid // 10, evening, 50, 500) if pid == 10 else response(pid, 2, evening, 0, 1000)

        collector.collect(self.catalog, self.data, fetch1, "20261030T000000Z")
        collector.collect(self.catalog, self.data, fetch2, "20261030T120000Z")
        pubs = collector.read_csv(self.data / "powerbi/daily_publishers.csv")
        self.assertEqual(len(pubs), 1)
        self.assertEqual((pubs[0]["games_monitored"], pubs[0]["games_on_sale"], pubs[0]["sale_share"]),
                         ("2", "1", "0.500000"))
        games = collector.read_csv(self.data / "powerbi/daily_games.csv")
        self.assertEqual([x["max_discount_percent"] for x in games], ["50", "0"])
        self.assertEqual([x["sample_count"] for x in games], ["2", "2"])
        self.assertEqual(len(collector.read_csv(self.data / "powerbi/snapshots.csv")), 4)
        self.assertEqual(len(list((self.data / "snapshots").glob("*.csv"))), 2)

    def test_missing_package_does_not_publish_partial_heatmap(self):
        def fetch(pid):
            r = response(pid, pid // 10, "2026-10-30T00:00:00+00:00")
            if pid == 20:
                r["body"] = {"20": {"success": False}}
            return r

        with self.assertRaises(collector.CollectionError):
            collector.collect(self.catalog, self.data, fetch, "20261030T000000Z")
        self.assertFalse((self.data / "powerbi/snapshots.csv").exists())
        self.assertTrue((self.data / "raw/20261030T000000Z.jsonl").exists())

    def test_package_without_target_app_is_rejected(self):
        r = response(10, 999, "2026-10-30T00:00:00+00:00")
        with self.assertRaises(collector.CollectionError):
            collector.parse_price(self.catalog[0], r, "20261030T000000Z")


if __name__ == "__main__":
    unittest.main()
