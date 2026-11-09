"""Confirm persistent US unavailability without treating request failures as delistings."""

from __future__ import annotations

import datetime as dt
from urllib.parse import urlencode


CONFIRM_DAYS = 3
JST = dt.timezone(dt.timedelta(hours=9))


def app_url(appid: int) -> str:
    return "https://store.steampowered.com/api/appdetails?" + urlencode(
        {"appids": appid, "cc": "us", "l": "english"}
    )


def store_entry(response: dict, key: int) -> dict:
    body = response.get("body")
    if response.get("http_status") != 200 or not isinstance(body, dict):
        return {}
    entry = body.get(str(key))
    return entry if isinstance(entry, dict) else {}


def purchase_signal(item: dict, app_response: dict, package_response: dict) -> str:
    appid, pid = int(item["appid"]), int(item["package_id"])
    app = store_entry(app_response, appid)
    pkg = store_entry(package_response, pid)
    if app.get("success") is False and pkg.get("success") is False:
        return "app_and_package_unavailable"
    data = app.get("data")
    if app.get("success") is not True or not isinstance(data, dict) or data.get("steam_appid") != appid:
        return "unknown"
    release = data.get("release_date")
    groups = data.get("package_groups")
    if not isinstance(release, dict) or not isinstance(groups, list):
        return "unknown"
    price = data.get("price_overview")
    if price is not None and not isinstance(price, dict):
        return "unknown"
    if price and price.get("currency") == "USD" and type(price.get("initial")) is int and price["initial"] > 0:
        return "available"
    for group in groups:
        if not isinstance(group, dict) or not isinstance(group.get("subs"), list):
            return "unknown"
        for option in group["subs"]:
            if not isinstance(option, dict):
                return "unknown"
            if type(option.get("packageid")) is int and option["packageid"] > 0:
                # A different package, bundle edition or a free licence is
                # not evidence that the game has been removed from sale.
                return "available"
            return "unknown"
    if release.get("coming_soon") is False and data.get("type") == "game" and data.get("is_free") is False and not price:
        return "released_without_purchase_offer"
    return "unknown"


def assess(catalog: list[dict], responses: list[dict], request, state: dict, run_date: str) -> tuple[dict, list, list]:
    checks = []
    for item, package in zip(catalog, responses):
        appid = int(item["appid"])
        response = request(app_url(appid))
        signal = purchase_signal(item, response, package)
        checks.append((item, signal, response))
    unavailable = {"app_and_package_unavailable", "released_without_purchase_offer"}
    absent_count = sum(signal in unavailable for _, signal, _ in checks)
    available_count = sum(signal == "available" for _, signal, _ in checks)
    # Require healthy storefront controls and quarantine widespread changes.
    safe = available_count >= max(1, (len(catalog) + 1) // 2) and absent_count <= max(1, len(catalog) // 10)
    pending = dict(state.get("pending", {}))
    retired = dict(state.get("retired", {}))
    confirmed = []
    if safe:
        today = dt.date.fromisoformat(run_date)
        for item, signal, response in checks:
            key = str(item["appid"])
            if signal == "available":
                pending.pop(key, None)
            if signal not in unavailable:
                continue
            entry = pending.get(key, {})
            dates = entry.get("dates", []) if entry.get("package_id") == str(item["package_id"]) else []
            # Preserve distinct observation dates; stale evidence does not accumulate.
            dates = [date for date in dates if dt.date.fromisoformat(date) >= today - dt.timedelta(days=4)]
            if run_date not in dates:
                dates.append(run_date)
            pending[key] = {"dates": sorted(dates), "package_id": str(item["package_id"]),
                            "signal": signal, "last_checked_utc": response.get("observed_at_utc")}
            if len(dates) >= CONFIRM_DAYS:
                confirmed.append(item)
    summary = {
        "date_jst": run_date, "safe_to_confirm": safe, "available": available_count,
        "unavailable": absent_count, "unknown": len(checks) - available_count - absent_count,
        "pending": pending, "retired": retired,
    }
    return summary, confirmed, checks


def retire(state: dict, items: list[dict]) -> None:
    for item in items:
        key = str(item["appid"])
        state["retired"][key] = {
            "effective_date_jst": state["date_jst"],
            "catalog_entry": item,
            "evidence": state["pending"].pop(key),
        }
