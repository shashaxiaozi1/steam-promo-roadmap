"""Track pinned Steam purchase packages and export Power BI-ready daily tables."""

from __future__ import annotations

import argparse
import concurrent.futures
import csv
import datetime as dt
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request


ROOT = Path(__file__).resolve().parent
CATALOG = ROOT / "catalog.csv"
DATA = ROOT / "data"
SNAPSHOT_FIELDS = [
    "run_id", "observed_at_utc", "date_jst", "publisher", "appid", "game_name",
    "tracked_edition", "package_id", "currency", "list_price_usd",
    "sale_price_usd", "discount_percent", "is_on_sale",
]
GAME_FIELDS = [
    "date_jst", "publisher", "appid", "game_name", "tracked_edition",
    "package_id", "sample_count", "max_discount_percent", "is_on_sale_any",
    "lowest_sale_price_usd", "first_observed_utc", "last_observed_utc",
]
PUBLISHER_FIELDS = [
    "date_jst", "publisher", "games_monitored", "games_on_sale",
    "sale_share", "sample_count_min", "sample_count_max",
]
_pace_lock = threading.Lock()
_next_request = 0.0


class CollectionError(Exception):
    pass


def timestamp() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def load_catalog(path: Path = CATALOG) -> list[dict]:
    with path.open(newline="", encoding="utf-8-sig") as f:
        items = list(csv.DictReader(f))
    if not items or len({int(x["appid"]) for x in items}) != len(items):
        raise CollectionError("Catalog is empty or has duplicate AppIDs")
    if len({int(x["package_id"]) for x in items}) != len(items):
        raise CollectionError("Package IDs must be unique in this MVP")
    return items


def fetch_package(package_id: int) -> dict:
    global _next_request
    url = "https://store.steampowered.com/api/packagedetails?" + urllib.parse.urlencode(
        {"packageids": package_id, "cc": "us", "l": "english"}
    )
    last = None
    for attempt in range(1, 4):
        with _pace_lock:
            pause = max(0, _next_request - time.monotonic())
            if pause:
                time.sleep(pause)
            _next_request = time.monotonic() + 1.25
        request = {"package_id": package_id, "url": url, "attempt": attempt}
        try:
            headers = {"User-Agent": "CapcomPriceTracker/0.1", "Accept": "application/json"}
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=30) as response:
                request.update(
                    http_status=response.status,
                    response_date=response.headers.get("Date"),
                    cache_age_seconds=response.headers.get("Age"),
                    body=json.load(response),
                )
        except urllib.error.HTTPError as exc:
            request.update(http_status=exc.code, error=str(exc))
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            request["error"] = str(exc)
        request["observed_at_utc"] = timestamp()
        last = request
        if "body" in request:
            break
        if request.get("http_status") in (401, 403, 404):
            break
        if attempt < 3:
            time.sleep(2 * attempt)
    return last


def parse_price(item: dict, response: dict, run_id: str) -> dict:
    package_id = int(item["package_id"])
    entry = (response.get("body") or {}).get(str(package_id), {})
    if not entry.get("success"):
        raise CollectionError(f"Package {package_id} did not return success=true")
    package = entry.get("data") or {}
    price = package.get("price") or {}
    app_ids = {x.get("id") for x in package.get("apps", [])}
    if int(item["appid"]) not in app_ids:
        raise CollectionError(f"AppID {item['appid']} absent from package {package_id}")
    initial, final, discount = (price.get(k) for k in ("initial", "final", "discount_percent"))
    if (price.get("currency") != "USD" or not all(type(x) is int for x in (initial, final, discount))
            or not (0 <= final <= initial and 0 <= discount <= 100)
            or abs(discount - (100 * (initial - final) / initial if initial else 0)) > 1):
        raise CollectionError(f"Package {package_id} has an invalid US price: {price}")
    observed = response["observed_at_utc"]
    date_jst = dt.datetime.fromisoformat(observed.replace("Z", "+00:00")).astimezone(
        dt.timezone(dt.timedelta(hours=9))
    ).date().isoformat()
    return {
        "run_id": run_id, "observed_at_utc": observed, "date_jst": date_jst,
        "publisher": item["publisher"], "appid": int(item["appid"]),
        "game_name": item["game_name"], "tracked_edition": item["tracked_edition"],
        "package_id": package_id, "currency": "USD",
        "list_price_usd": f"{initial / 100:.2f}",
        "sale_price_usd": f"{final / 100:.2f}",
        "discount_percent": discount, "is_on_sale": int(discount > 0),
    }


def atomic_csv(path: Path, fields: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".tmp-", dir=path.parent, text=True)
    try:
        with os.fdopen(fd, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def rollups(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    by_game: dict[tuple, list[dict]] = {}
    for x in rows:
        by_game.setdefault((x["date_jst"], x["publisher"], str(x["appid"])), []).append(x)
    games = []
    for key, samples in sorted(by_game.items()):
        samples.sort(key=lambda x: x["observed_at_utc"])
        latest = samples[-1]
        games.append({
            "date_jst": key[0], "publisher": key[1], "appid": key[2],
            "game_name": latest["game_name"], "tracked_edition": latest["tracked_edition"],
            "package_id": latest["package_id"], "sample_count": len(samples),
            "max_discount_percent": max(int(x["discount_percent"]) for x in samples),
            "is_on_sale_any": int(any(int(x["is_on_sale"]) for x in samples)),
            "lowest_sale_price_usd": f"{min(float(x['sale_price_usd']) for x in samples):.2f}",
            "first_observed_utc": samples[0]["observed_at_utc"],
            "last_observed_utc": samples[-1]["observed_at_utc"],
        })
    by_pub: dict[tuple, list[dict]] = {}
    for x in games:
        by_pub.setdefault((x["date_jst"], x["publisher"]), []).append(x)
    publishers = []
    for (date, name), games_on_date in sorted(by_pub.items()):
        count = len(games_on_date)
        on_sale = sum(int(x["is_on_sale_any"]) for x in games_on_date)
        samples = [int(x["sample_count"]) for x in games_on_date]
        publishers.append({
            "date_jst": date, "publisher": name, "games_monitored": count,
            "games_on_sale": on_sale, "sale_share": f"{on_sale / count:.6f}",
            "sample_count_min": min(samples), "sample_count_max": max(samples),
        })
    return games, publishers


def collect(catalog: list[dict], output: Path = DATA, fetch=fetch_package, run_id: str | None = None) -> dict:
    run_id = run_id or dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    if not (len(run_id) == 16 and run_id.endswith("Z") and run_id[8] == "T"):
        raise CollectionError("Invalid run_id")
    snapshot = output / "snapshots" / (run_id + ".csv")
    if snapshot.exists():
        raise CollectionError(f"Run already exists: {run_id}")
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda x: fetch(int(x["package_id"])), catalog))
    raw = output / "raw" / (run_id + ".jsonl")
    raw.parent.mkdir(parents=True, exist_ok=True)
    with raw.open("x", encoding="utf-8") as f:
        for item, response in zip(catalog, responses):
            f.write(json.dumps({"appid": int(item["appid"]), **response}, ensure_ascii=False) + "\n")
    rows, errors = [], []
    for item, response in zip(catalog, responses):
        try:
            rows.append(parse_price(item, response, run_id))
        except (CollectionError, KeyError, TypeError) as exc:
            errors.append(str(exc))
    if errors:
        raise CollectionError(f"Incomplete run ({len(errors)} errors); raw saved at {raw}: " + "; ".join(errors))
    old = read_csv(output / "powerbi" / "snapshots.csv")
    if any(x["run_id"] == run_id for x in old):
        raise CollectionError(f"Run already in history: {run_id}")
    combined = sorted(old + rows, key=lambda x: (x["run_id"], int(x["appid"])))
    games, pubs = rollups(combined)
    atomic_csv(snapshot, SNAPSHOT_FIELDS, rows)
    atomic_csv(output / "powerbi" / "snapshots.csv", SNAPSHOT_FIELDS, combined)
    atomic_csv(output / "powerbi" / "daily_games.csv", GAME_FIELDS, games)
    atomic_csv(output / "powerbi" / "daily_publishers.csv", PUBLISHER_FIELDS, pubs)
    return {"run_id": run_id, "rows": len(rows), "on_sale": sum(x["is_on_sale"] for x in rows),
            "raw": str(raw), "snapshot": str(snapshot), "history_rows": len(combined)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, default=CATALOG)
    parser.add_argument("--output", type=Path, default=DATA)
    args = parser.parse_args()
    try:
        print(json.dumps(collect(load_catalog(args.catalog), args.output), ensure_ascii=False))
    except CollectionError as exc:
        parser.exit(1, str(exc) + "\n")
