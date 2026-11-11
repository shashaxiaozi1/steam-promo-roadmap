"""Import one reviewed SteamDB USD/UTC history and rebuild Power BI daily views."""

import argparse
import hashlib
from pathlib import Path

import collector as c
from price_history import reconstruct


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--appid", type=int, required=True)
    parser.add_argument("--currency", choices=["USD"], required=True)
    parser.add_argument("--source-timezone", choices=["UTC"], required=True)
    parser.add_argument("--list-price-usd", required=True,
                        help="Reviewed unchanged list price for this source's coverage")
    parser.add_argument("--output", type=Path, default=c.DATA)
    args = parser.parse_args()
    items = [x for x in c.load_catalog(args.catalog) if int(x["appid"]) == args.appid]
    if len(items) != 1:
        parser.error("AppID must be in the active, verified tracking catalog")
    item = items[0]
    if str(args.appid) in c.read_state(c.lifecycle_path(args.output, item["publisher"])).get("retired", {}):
        parser.error("Retired AppID cannot be automatically reintroduced")
    days, intervals, manifest = reconstruct(args.input, item, args.list_price_usd)
    payload = args.input.read_bytes()
    manifest.update(appid=args.appid, publisher=item["publisher"], game_name=item["game_name"],
                    package_id=int(item["package_id"]), tracked_edition=item["tracked_edition"],
                    source="User-provided unmodified SteamDB export", source_sha256=hashlib.sha256(payload).hexdigest())
    dest = args.output / "history" / str(args.appid)
    previous = c.read_state(dest / "manifest.json")
    if previous and previous != manifest:
        parser.error("Different history already imported for this AppID; review replacement coverage first")
    # Validate every output before replacing the existing daily reports.
    rows = c.read_csv(args.output / "powerbi" / "snapshots.csv")
    live, _ = c.rollups(c.report_rows(rows, args.output, c.REPORT_START_RUN_ID))
    others = [r for p in sorted((args.output / "history").glob("*/daily_games.csv"))
              if p.parent != dest for r in c.read_csv(p)]
    from price_history import merge_daily
    merge_daily(live, others + days)
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "source.csv").write_bytes(payload)
    c.atomic_csv(dest / "daily_games.csv", [*c.GAME_FIELDS, "coverage_start_utc", "coverage_end_utc_exclusive"], days)
    c.atomic_csv(dest / "intervals.csv", list(intervals[0]), intervals)
    c.write_json(dest / "manifest.json", manifest)
    games, pubs = c.build_reports(rows, args.output)
    c.atomic_csv(args.output / "powerbi" / "daily_games.csv", c.GAME_FIELDS, games)
    c.atomic_csv(args.output / "powerbi" / "daily_publishers.csv", c.PUBLISHER_FIELDS, pubs)
    print(f"Imported {len(days)} JST days; {manifest['sale_days']} days include a sale")


if __name__ == "__main__":
    main()
