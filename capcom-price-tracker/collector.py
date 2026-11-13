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

from publisher_config import PUBLISHER_FILES


ROOT = Path(__file__).resolve().parent
CATALOG = ROOT / "catalog.csv"
DATA = ROOT / "data"
# First successful run after the one-time 49 → 73 reconciliation. Older
# observations remain in snapshots.csv but do not re-enter the published series.
REPORT_START_RUN_ID = "20261106T061310Z"
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


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".tmp-", dir=path.parent, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def lifecycle_path(output: Path, publisher: str) -> Path:
    return output / "lifecycle" / (publisher.lower().replace(" ", "_") + ".json")


def read_state(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def retirement_stops(output: Path, current_state: dict | None = None) -> dict:
    stops = {}
    states = [read_state(p) for p in (output / "lifecycle").glob("*.json")]
    if current_state is not None:
        states.append(current_state)
    for state in states:
        for appid, entry in state.get("retired", {}).items():
            stops[(entry["catalog_entry"]["publisher"], appid)] = entry["effective_date_jst"]
    return stops


def report_rows(rows: list[dict], output: Path, report_start: str, current_state: dict | None = None) -> list[dict]:
    stops = retirement_stops(output, current_state)
    return [x for x in rows if x["run_id"] >= report_start
            and x["date_jst"] < stops.get((x["publisher"], str(x["appid"])), "9999-12-31")]


def load_catalog(path: Path = CATALOG) -> list[dict]:
    with path.open(newline="", encoding="utf-8-sig") as f:
        items = list(csv.DictReader(f))
    if not items or len({int(x["appid"]) for x in items}) != len(items):
        raise CollectionError("Catalog is empty or has duplicate AppIDs")
    if len({int(x["package_id"]) for x in items}) != len(items):
        raise CollectionError("Package IDs must be unique in this MVP")
    return items


def active_catalog(items: list[dict], state: dict) -> list[dict]:
    return [r for r in items if r.get("tracking_status", "active") in ("", "active")
            and str(r["appid"]) not in state.get("retired", {})]


def mark_retired_entries(items: list[dict], state: dict) -> list[dict]:
    result = []
    for row in items:
        entry = state.get("retired", {}).get(str(row["appid"]))
        if entry is None:
            result.append(dict(row))
        else:
            result.append(dict(row, tracking_status="retired",
                               retired_at_utc=entry.get("retired_at_utc") or "",
                               retirement_effective_date_jst=entry["effective_date_jst"],
                               retirement_reason=entry.get("reason", "confirmed_US_purchase_unavailable")))
    return result


def fetch_package(package_id: int) -> dict:
    url = "https://store.steampowered.com/api/packagedetails?" + urllib.parse.urlencode(
        {"packageids": package_id, "cc": "us", "l": "english"}
    )
    return {"package_id": package_id, **fetch_json(url)}


def fetch_json(url: str) -> dict:
    global _next_request
    last = None
    for attempt in range(1, 4):
        with _pace_lock:
            pause = max(0, _next_request - time.monotonic())
            if pause:
                time.sleep(pause)
            _next_request = time.monotonic() + 1.25
        request = {"url": url, "attempt": attempt}
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


def choose_edition(item: dict, options: list[dict], fetch, app_request, run_id: str,
                   *, linked_dlc=()) -> tuple[dict, dict]:
    """Choose the lowest US list price among verifiable full-game editions."""
    from availability import app_url

    appid = int(item["appid"])
    extras = {}
    verified = []
    for offer in options:
        pid = offer["package_id"]
        selected = {**item, "package_id": pid, "tracked_edition": offer["label"]}
        response = fetch(pid)
        row = parse_price(selected, response, run_id)
        package = response["body"][str(pid)]["data"]
        price = package["price"]
        if price["initial"] <= 0 or price["final"] != offer["store_option_price_cents"]:
            raise CollectionError("edition_offer_and_package_price_differ")
        for extra in {app["id"] for app in package["apps"]} - {appid} - set(linked_dlc):
            if extra not in extras:
                extra_response = app_request(app_url(extra)) if app_request is not None else {}
                entry = (extra_response.get("body") or {}).get(str(extra)) or {}
                extras[extra] = (entry.get("data") or {}) if entry.get("success") is True else {}
            extra_app = extras[extra]
            if (extra_app.get("steam_appid") != extra or extra_app.get("type") != "dlc"
                    or str((extra_app.get("fullgame") or {}).get("appid")) != str(appid)):
                raise CollectionError("edition_package_contains_unverified_or_unrelated_apps")
        verified.append((price["initial"], selected, row))
    verified.sort(key=lambda x: x[0])
    if not verified or (len(verified) > 1 and verified[0][0] == verified[1][0]):
        raise CollectionError("cheapest_US_full_edition_not_unique")
    cents, selected, row = verified[0]
    selected["tracking_note"] = (
        f"No standard US offer; cheapest verified full edition at US list price ${cents / 100:.2f}: "
        f"{selected['tracked_edition']}"
    )
    return selected, row


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
    return games, publisher_rollups(games)


def publisher_rollups(games: list[dict]) -> list[dict]:
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
    return publishers


def build_reports(rows: list[dict], output: Path, report_start: str = REPORT_START_RUN_ID,
                  current_state: dict | None = None, *, include_coverage: bool = False):
    from price_history import merge_daily

    live, _ = rollups(report_rows(rows, output, report_start, current_state))
    historical = [row for path in sorted((output / "history").glob("*/daily_games.csv"))
                  for row in read_csv(path)]
    from capcom_history import load_details, publisher_history, export_prefix
    from publisher_config import PUBLISHERS
    reviewed = []
    for publisher, (stem, _, _) in PUBLISHERS.items():
        folder = output / 'history' / stem
        publisher_days, history_plans, history_metadata = load_details(folder, publisher)
        if not history_plans:
            continue
        reviewed.append((publisher, folder, history_plans, history_metadata))
        first_live = {}
        for row in live:
            if row['publisher'] == publisher:
                aid = str(row['appid'])
                first_live[aid] = min(first_live.get(aid, row['date_jst']), row['date_jst'])
        for row in publisher_days:
            if row['date_jst'] < first_live.get(str(row['appid']), '9999-12-31'):
                row['package_id'] = ''
                row['tracked_edition'] = history_plans[str(row['appid'])]['historical_edition']
        historical.extend(publisher_days)
    stops = retirement_stops(output, current_state)
    historical = [x for x in historical if x["date_jst"] <
                  stops.get((x["publisher"], str(x["appid"])), "9999-12-31")]
    try:
        merged_days = merge_daily(live, historical)
        games = [{field: row[field] for field in GAME_FIELDS} for row in merged_days]
    except ValueError as exc:
        raise CollectionError(str(exc)) from exc
    # Single-game backfills do not imply complete publisher coverage.
    live_keys = {(x["date_jst"], x["publisher"], str(x["appid"])) for x in live}
    pubs = publisher_rollups([x for x in games
                             if (x["date_jst"], x["publisher"], str(x["appid"])) in live_keys])
    from collection_coverage import build_coverage
    coverage = build_coverage(live, output, report_start, stops, current_state)
    by_key = {(p["date_jst"], p["publisher"]): p for p in pubs}
    for record in coverage:
        if record["is_complete"]:
            continue
        k = record["date_jst"], record["publisher"]
        prior = by_key.get(k, {})
        by_key[k] = dict(date_jst=k[0], publisher=k[1], games_monitored=record["expected_games"],
                         games_on_sale="", sale_share="", sample_count_min=0,
                         sample_count_max=prior.get("sample_count_max", 0))
    pubs = [by_key[k] for k in sorted(by_key)]
    for publisher, folder, history_plans, history_metadata in reviewed:
        history_pubs, history_coverage, gaps = publisher_history(
            merged_days, live, folder, stops, history_plans, history_metadata, coverage, publisher=publisher)
        pubs = sorted([p for p in pubs if p['publisher'] != publisher] + history_pubs,
                      key=lambda p: (p['date_jst'], p['publisher']))
        coverage = sorted([p for p in coverage if p['publisher'] != publisher] + history_coverage,
                          key=lambda p: (p['date_jst'], p['publisher']))
        atomic_csv(output / 'powerbi' / (export_prefix(publisher, folder) + 'historical_coverage_gaps.csv'),
                   ['appid', 'game_name', 'start_date_jst', 'end_date_jst', 'days', 'reason'], gaps)
    return (games, pubs, coverage) if include_coverage else (games, pubs)


def collect(catalog: list[dict], output: Path = DATA, fetch=fetch_package, run_id: str | None = None,
            *, discover=None, catalog_path: Path | None = None,
            report_start: str = REPORT_START_RUN_ID, availability_request=None, edition_request=None) -> dict:
    if (discover is not None or availability_request is not None) and catalog_path is None:
        raise CollectionError("Catalog updates require a writable catalog path")
    publishers = {x["publisher"] for x in catalog}
    if len(publishers) != 1:
        raise CollectionError("Collect one publisher at a time")
    publisher = next(iter(publishers))
    state_path = lifecycle_path(output, publisher)
    state = read_state(state_path)
    original_catalog = list(catalog)
    catalog = active_catalog(catalog, state)
    run_id = run_id or dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    if not (len(run_id) == 16 and run_id.endswith("Z") and run_id[8] == "T"):
        raise CollectionError("Invalid run_id")
    snapshot = output / "snapshots" / (run_id + ".csv")
    if snapshot.exists():
        raise CollectionError(f"Run already exists: {run_id}")
    def request_price(item):
        try:
            return fetch(int(item["package_id"]))
        except (OSError, ValueError) as exc:
            return {"package_id": int(item["package_id"]), "error": str(exc), "observed_at_utc": timestamp()}

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(request_price, catalog))
    raw = output / "raw" / (run_id + ".jsonl")
    raw.parent.mkdir(parents=True, exist_ok=True)
    with raw.open("x", encoding="utf-8") as f:
        for item, response in zip(catalog, responses):
            f.write(json.dumps({"appid": int(item["appid"]), **response}, ensure_ascii=False) + "\n")
    confirmed = []
    run_date = dt.datetime.strptime(run_id, "%Y%m%dT%H%M%SZ").replace(tzinfo=dt.timezone.utc).astimezone(
        dt.timezone(dt.timedelta(hours=9))).date().isoformat()
    if availability_request is not None:
        from availability import assess
        state, confirmed, checks = assess(catalog, responses, availability_request, state, run_date)
        with raw.open("a", encoding="utf-8") as f:
            for item, signal, response in checks:
                if signal != "available":
                    f.write(json.dumps({"appid": int(item["appid"]), "availability_signal": signal,
                                        **response}, ensure_ascii=False) + "\n")
        # Persist availability evidence independently of individual price failures.
        write_json(state_path, state)
    removed = {int(x["appid"]) for x in confirmed}
    rows, errors = [], []
    for item, response in zip(catalog, responses):
        try:
            rows.append(parse_price(item, response, run_id))
        except (CollectionError, KeyError, TypeError) as exc:
            if int(item["appid"]) not in removed:
                errors.append({"appid": int(item["appid"]), "error": str(exc)})
    if rows and len({row["date_jst"] for row in rows}) != 1:
        raise CollectionError(f"Run crossed midnight JST; raw saved at {raw}; retry for a full day")
    if rows:
        run_date = rows[0]["date_jst"]
    old = read_csv(output / "powerbi" / "snapshots.csv")
    if any(x["run_id"] == run_id for x in old):
        raise CollectionError(f"Run already in history: {run_id}")
    additions = []
    discovery_report = None
    if discover is not None:
        known = {int(x["appid"]) for x in original_catalog} | {int(x) for x in state.get("retired", {})}
        proposed, discovery_report = discover(known)
        used_packages = {int(x["package_id"]) for x in original_catalog}
        used_apps = {int(x["appid"]) for x in original_catalog}
        with raw.open("a", encoding="utf-8") as f:
            for item, decision in proposed:
                appid = int(item["appid"])
                if appid in used_apps or (item["package_id"] is not None and int(item["package_id"]) in used_packages):
                    decision.update(status="review", reason="package_or_app_already_tracked")
                    continue
                def fetch_candidate(pid):
                    response = fetch(pid)
                    f.write(json.dumps({"appid": appid, **response}, ensure_ascii=False) + "\n")
                    return response

                try:
                    if decision.get("edition_options"):
                        item, row = choose_edition(item, decision["edition_options"], fetch_candidate, edition_request,
                                                   run_id, linked_dlc=decision.get("linked_dlc_appids", ()))
                        decision.update(package_id=item["package_id"], tracked_edition=item["tracked_edition"],
                                        tracking_note=item["tracking_note"])
                    else:
                        pid = int(item["package_id"])
                        response = fetch_candidate(pid)
                        row = parse_price(item, response, run_id)
                        package = response["body"][str(pid)]["data"]
                        if {a.get("id") for a in package["apps"]} != {appid}:
                            raise CollectionError("package_contains_other_apps")
                        if package["price"]["initial"] <= 0:
                            raise CollectionError("no_positive_USD_list_price")
                        if package["price"]["final"] != decision["store_option_price_cents"]:
                            raise CollectionError("purchase_option_and_package_price_differ")
                    pid = int(item["package_id"])
                    if pid in used_packages:
                        raise CollectionError("package_or_app_already_tracked")
                    if row["date_jst"] != run_date:
                        raise CollectionError("first_price_crossed_midnight_JST")
                except (CollectionError, KeyError, TypeError, ValueError, AttributeError) as exc:
                    decision.update(status="review", reason=str(exc))
                    continue
                rows.append(row)
                additions.append(item)
                used_packages.add(pid)
                used_apps.add(appid)
                decision.update(status="added", first_sample_date_jst=row["date_jst"])
    combined = sorted(old + rows, key=lambda x: (x["run_id"], int(x["appid"])))
    if confirmed:
        from availability import retire
        retire(state, confirmed, observations=combined)
    status = {"run_id": run_id, "date_jst": run_date, "publisher": publisher,
              "expected_appids": [int(x["appid"]) for x in catalog + additions],
              "successful_appids": [int(x["appid"]) for x in rows], "errors": errors,
              "status": "partial" if errors and rows else "failed" if errors else "complete"}
    write_json(output / "collection_status" / (run_id + ".json"), status)
    games, pubs, coverage = build_reports(combined, output, report_start, state, include_coverage=True)
    if additions or confirmed:
        with catalog_path.open(newline="", encoding="utf-8-sig") as f:
            catalog_fields = next(csv.reader(f))
        catalog_fields = list(dict.fromkeys([*catalog_fields, "steam_store_release_date"]))
        if any("tracking_note" in item for item in additions) and "tracking_note" not in catalog_fields:
            catalog_fields.append("tracking_note")
        saved_catalog = mark_retired_entries(original_catalog + additions, state)
        if confirmed:
            catalog_fields = list(dict.fromkeys([*catalog_fields, "tracking_status", "retired_at_utc",
                                                "retirement_effective_date_jst", "retirement_reason"]))
        atomic_csv(catalog_path, catalog_fields, saved_catalog)
    if confirmed:
        write_json(state_path, state)
    if discovery_report is not None:
        name = PUBLISHER_FILES[publisher][2]
        write_json(output / "discovery" / name, {"run_id": run_id, **discovery_report})
    atomic_csv(snapshot, SNAPSHOT_FIELDS, rows)
    atomic_csv(output / "powerbi" / "snapshots.csv", SNAPSHOT_FIELDS, combined)
    atomic_csv(output / "powerbi" / "daily_games.csv", GAME_FIELDS, games)
    atomic_csv(output / "powerbi" / "daily_publishers.csv", PUBLISHER_FIELDS, pubs)
    from collection_coverage import FIELDS
    atomic_csv(output / "powerbi" / "collection_coverage.csv", FIELDS, coverage)
    return {"run_id": run_id, "rows": len(rows), "on_sale": sum(x["is_on_sale"] for x in rows),
            "raw": str(raw), "snapshot": str(snapshot), "history_rows": len(combined),
            "added_appids": [int(x["appid"]) for x in additions],
            "publisher": publisher, "retired_appids": sorted(removed),
            "status": status["status"], "collection_errors": errors,
            "discovery_errors": (discovery_report or {}).get("errors", [])}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path)
    parser.add_argument("--output", type=Path, default=DATA)
    parser.add_argument("--publisher", choices=tuple(PUBLISHER_FILES), default="CAPCOM")
    parser.add_argument("--discover", action="store_true", help="Check Steam for eligible releases")
    parser.add_argument("--audit-availability", action="store_true", help="Check the active US purchase offers")
    args = parser.parse_args()
    try:
        catalog_name, excluded_name, _ = PUBLISHER_FILES[args.publisher]
        catalog_path = args.catalog or ROOT / catalog_name
        catalog = load_catalog(catalog_path)
        if any(row["publisher"] != args.publisher for row in catalog):
            raise CollectionError("Catalog publisher does not match --publisher")
        discover = None
        if args.discover:
            from discovery import scan
            excluded_path = ROOT / excluded_name
            excluded = {int(x["appid"]) for x in read_csv(excluded_path)}
            names = tuple(item["game_name"] for item in catalog)
            discover = lambda known: scan(known | excluded, fetch_json, tracked_names=names, publisher=args.publisher)
        result = collect(catalog, args.output, discover=discover,
                                 catalog_path=catalog_path,
                                 availability_request=fetch_json if args.audit_availability else None,
                                 edition_request=fetch_json)
        print(json.dumps(result, ensure_ascii=False))
        if result["status"] == "partial":
            print(f"::warning::{args.publisher}: {len(result['collection_errors'])} games lack valid prices; see collection_status and coverage exports")
        elif result["status"] == "failed":
            parser.exit(1, "No valid prices collected; expected game coverage retained for review\n")
    except CollectionError as exc:
        parser.exit(1, str(exc) + "\n")
