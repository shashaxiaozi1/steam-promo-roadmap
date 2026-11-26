"""Capture the Steam US storefront price for the weekly-chart watchlist."""

import argparse
from concurrent.futures import ThreadPoolExecutor
import csv
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo


JST = ZoneInfo("Asia/Tokyo")
FIELDS = [
    "observed_at_utc", "date_jst", "appid", "game_name", "currency",
    "list_price_usd", "sale_price_usd", "discount_percent", "price_source", "status",
]


def read_watchlist(path):
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows or any(not re.fullmatch(r"[1-9][0-9]*", r["appid"]) for r in rows):
        raise ValueError("Expected a nonempty list of positive Steam AppIDs")
    if len({row["appid"] for row in rows}) != len(rows):
        raise ValueError("AppIDs must be unique")
    return rows


def app_details(appid):
    url = f"https://store.steampowered.com/api/appdetails?appids={appid}&cc=us&l=english"
    request = Request(url, headers={"Accept": "application/json", "User-Agent": "SteamWeeklyChartWatch/1.0"})
    with urlopen(request, timeout=15) as response:
        result = json.load(response).get(appid, {})
    return result.get("data") if result.get("success") else None


def observation(item, app, observed_at):
    row = {
        "observed_at_utc": observed_at.isoformat(timespec="seconds"),
        "date_jst": observed_at.astimezone(JST).date().isoformat(),
        "appid": item["appid"],
        "game_name": (app or {}).get("name") or item["name"],
        "price_source": "steam_us_appdetails_price_overview",
        "status": "no_us_app_details",
    }
    if app is None:
        return row
    if str(app.get("steam_appid")) != item["appid"] or app.get("type") != "game" or app.get("is_free") is not False:
        row["status"] = "paid_base_game_not_confirmed"
        return row
    quote = app.get("price_overview") or {}
    initial, final, discount = (quote.get(key) for key in ("initial", "final", "discount_percent"))
    if (quote.get("currency") != "USD" or not all(type(n) is int for n in (initial, final, discount))
            or not (0 <= final <= initial and initial > 0 and 0 <= discount <= 100)
            or abs(discount - 100 * (initial - final) / initial) > 1):
        row["status"] = "no_valid_us_storefront_price"
        return row
    row.update(
        currency="USD",
        list_price_usd=f"{initial / 100:.2f}",
        sale_price_usd=f"{final / 100:.2f}",
        discount_percent=discount,
        status="ok",
    )
    return row


def collect(items, destination):
    stopped = threading.Event()
    pace = threading.Lock()
    next_request = 0.0

    def sample(item):
        nonlocal next_request
        with pace:
            if stopped.is_set():
                return None
            time.sleep(max(0.0, next_request - time.monotonic()))
            if stopped.is_set():
                return None
            next_request = time.monotonic() + 1.25
        error = ""
        try:
            app = app_details(item["appid"])
        except HTTPError as exc:
            app = None
            error = f"http_{exc.code}"
            if exc.code in (403, 429):
                stopped.set()
        except (TimeoutError, URLError, ValueError):
            app = None
            error = "request_failed"
        row = observation(item, app, datetime.now(timezone.utc))
        if error:
            row["status"] = error
        return row

    valid = 0
    attempted = 0
    with destination.open("x", encoding="utf-8", newline="") as handle, ThreadPoolExecutor(max_workers=4) as pool:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        for row in pool.map(sample, items):
            if row is None:
                continue
            attempted += 1
            writer.writerow(row)
            handle.flush()
            valid += row["status"] == "ok"
            if attempted % 20 == 0 or row["status"] != "ok":
                print(f"{attempted}/{len(items)} AppID {row['appid']}: {row['status']}", flush=True)
    if stopped.is_set():
        print("Collection stopped after an access or rate-limit response.", flush=True)
    print(f"Collected {valid} usable prices across {attempted} attempts for {len(items)} AppIDs.", flush=True)
    return 2 if stopped.is_set() or not valid else 0


def main():
    directory = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--watchlist", type=Path, default=directory / "watchlist.csv")
    parser.add_argument("--output-dir", type=Path, default=directory / "data")
    args = parser.parse_args()
    items = read_watchlist(args.watchlist)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    destination = args.output_dir / f"{stamp}.csv"
    return collect(items, destination)


if __name__ == "__main__":
    raise SystemExit(main())
