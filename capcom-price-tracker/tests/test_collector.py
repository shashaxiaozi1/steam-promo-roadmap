import csv
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from urllib.parse import parse_qs, urlsplit


def load_module(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).parents[1] / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


collector = load_module("collector")
discovery = load_module("discovery")


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

        collector.collect(self.catalog, self.data, fetch1, "20261107T000000Z")
        collector.collect(self.catalog, self.data, fetch2, "20261107T120000Z")
        pubs = collector.read_csv(self.data / "powerbi/daily_publishers.csv")
        self.assertEqual(len(pubs), 1)
        self.assertEqual((pubs[0]["games_monitored"], pubs[0]["games_on_sale"], pubs[0]["sale_share"]),
                         ("2", "1", "0.500000"))
        games = collector.read_csv(self.data / "powerbi/daily_games.csv")
        self.assertEqual([x["max_discount_percent"] for x in games], ["50", "0"])
        self.assertEqual([x["sample_count"] for x in games], ["2", "2"])
        self.assertEqual(len(collector.read_csv(self.data / "powerbi/snapshots.csv")), 4)
        self.assertEqual(len(list((self.data / "snapshots").glob("*.csv"))), 2)

    def test_catalog_expansion_preserves_published_dates_and_excludes_legacy_run(self):
        old_catalog = self.catalog[:1]
        collector.collect(old_catalog, self.data,
                          lambda pid: response(pid, 1, "2026-11-06T00:00:00+00:00"),
                          "20261106T000000Z")
        collector.collect(old_catalog, self.data,
                          lambda pid: response(pid, 1, "2026-11-06T06:13:10+00:00"),
                          "20261106T061310Z")
        collector.collect(self.catalog, self.data,
                          lambda pid: response(pid, pid // 10, "2026-11-07T00:00:00+00:00"),
                          "20261107T000000Z")
        self.assertEqual(len(collector.read_csv(self.data / "powerbi/snapshots.csv")), 4)
        pubs = collector.read_csv(self.data / "powerbi/daily_publishers.csv")
        self.assertEqual([(x["date_jst"], x["games_monitored"]) for x in pubs],
                         [("2026-11-06", "1"), ("2026-11-07", "2")])
        self.assertEqual(pubs[0]["sample_count_max"], "1")

    def test_discovery_adds_verified_preorder_on_first_price_and_isolates_exceptions(self):
        path = self.data / "catalog.csv"
        collector.atomic_csv(path, list(self.catalog[0]) + ["release_date_source"], self.catalog)
        collector.collect(self.catalog, self.data,
                          lambda pid: response(pid, pid // 10, "2026-11-08T12:00:00+00:00", 0, 1000),
                          "20261108T120000Z")

        def store(url):
            link = urlsplit(url)
            query = parse_qs(link.query)
            if link.path == "/search/results/":
                ids = (1, 2, 3, 4, 5, 6, 7, 8)
                html = "".join(f'<a class="search_result_row" href="/app/{i}/title/"></a>' for i in ids)
                html += '<a class="search_result_row" href="/bundle/99/example/"></a>'
                return {"body": {"total_count": 9, "results_html": html}}
            appid = int(query["appids"][0])
            title = f"Game {appid}"
            subs = [{"packageid": appid * 10, "option_text": f"Pre-Purchase {title} - $5.00",
                     "is_free_license": False, "price_in_cents_with_discount": 500}]
            if appid == 5:
                subs.append({**subs[0], "packageid": 51})
            if appid == 8:
                subs[0].update(option_text=f"{title} - $10.00", price_in_cents_with_discount=1000)
            app = {
                "steam_appid": appid, "name": title, "type": "game", "is_free": False,
                "publishers": ["CAPCOM Co., Ltd."],
                "release_date": {"coming_soon": appid in (3, 4), "date": "Nov 7, 2026"},
                "package_groups": [] if appid == 4 else [{"subs": subs}],
            }
            if appid == 7:
                app["release_date"]["date"] = "Jan 1, 2015"
            return {"body": {str(appid): {"success": True, "data": app}}}

        def fetch(pid):
            result = response(pid, pid // 10, "2026-11-09T00:00:00+00:00",
                              50 if pid in (30, 60) else 0, 500 if pid in (30, 60) else 1000)
            if pid == 60:
                result["body"]["60"]["data"]["apps"].append({"id": 99})
            return result

        result = collector.collect(self.catalog, self.data, fetch, "20261109T000000Z",
                                   discover=lambda known: discovery.scan(known, store), catalog_path=path)
        self.assertEqual(result["added_appids"], [3, 8])
        self.assertEqual([int(x["appid"]) for x in collector.load_catalog(path)], [1, 2, 3, 8])
        pubs = collector.read_csv(self.data / "powerbi/daily_publishers.csv")
        self.assertEqual([(x["games_monitored"], x["games_on_sale"], x["sale_share"]) for x in pubs],
                         [("2", "0", "0.000000"), ("4", "1", "0.250000")])
        report = json.loads((self.data / "discovery/latest.json").read_text())
        self.assertEqual({x["appid"]: x["status"] for x in report["candidates"]},
                         {3: "added", 4: "pending", 5: "review", 6: "review", 7: "review", 8: "added"})

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
