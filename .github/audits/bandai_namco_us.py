"""Read-only US storefront audit for Bandai Namco Entertainment."""
from __future__ import annotations

import json
from pathlib import Path
import sys
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "capcom-price-tracker"))
from collector import CollectionError, fetch_json, fetch_package, parse_price, timestamp
from discovery import DiscoveryError, STORE, name_key, search_publisher, standard_options

PUBLISHERS = (
    "Bandai Namco Entertainment", "Bandai Namco Entertainment Inc.",
    "Bandai Namco Games", "Bandai Namco Games Inc.",
    "Namco Bandai Games", "Namco Bandai Games Inc.",
)
ACCEPTED = {name_key(s) for s in PUBLISHERS}
# The search input can separate publisher names that differ only by case.
SEARCH_NAMES = ("BANDAI NAMCO Entertainment", "BANDAI NAMCO Entertainment Inc.",
                "BANDAI NAMCO ENTERTAINMENT", "BANDAI NAMCO ENTERTAINMENT INC.",
                "Bandai Namco Entertainment, Inc.", "BANDAI NAMCO Games",
                "BANDAI NAMCO Games Inc.", "NAMCO BANDAI Games",
                "NAMCO BANDAI Games Inc.")
ALREADY_AUDITED = {335300, 374320, 454650, 502500, 525480, 570940, 633230, 678950, 678960, 740130, 814000, 824550, 851850, 871980, 898750, 1020790, 1245480, 1245620, 1276760, 1367080, 1372110, 1392860, 1492730, 1530160, 1566880, 1607250, 1665130, 1672500, 1689620, 1730680, 1730700, 1778820, 1790600, 1857740, 1857810, 1858630, 1859430, 1859470, 1877020, 1880620, 1888160, 1909950, 1910090, 1963680, 1979440, 1984270, 2072560, 2146170, 2170370, 2172910, 2197680, 2213190, 2244210, 2246670, 2288340, 2288630, 2303000, 2324290, 2331020, 2362050, 2362060, 2371630, 2383200, 2530980, 2622380, 2730810, 2918150, 3004100, 3470960, 3751260, 3905010}


def audit(appid, search_names):
    row = dict(appid=appid, search_names=search_names, checked_at=timestamp())
    def result(status, reason, **other):
        return dict(row, status=status, reason=reason, **other)
    url = STORE + "/api/appdetails?" + urlencode({"appids": appid, "cc": "us", "l": "english"})
    response = fetch_json(url)
    entry = (response.get("body") or {}).get(str(appid)) or {}
    app = entry.get("data")
    if not entry.get("success") or not isinstance(app, dict):
        return result("review", "US_appdetails_unavailable", http_status=response.get("http_status"))
    row.update(title=app.get("name"), publishers=app.get("publishers") or [],
               release_text=(app.get("release_date") or {}).get("date"),
               coming_soon=(app.get("release_date") or {}).get("coming_soon"),
               app_type=app.get("type"), is_free=app.get("is_free"))
    if app.get("steam_appid") != appid or not row["title"]:
        return result("review", "app_identity_mismatch")
    if not any(name_key(p) in ACCEPTED for p in row["publishers"]):
        return result("exclude", "different_Steam_publisher")
    if app.get("type") != "game":
        return result("exclude", "not_base_game")
    if app.get("is_free") is True:
        return result("exclude", "free_base_game")
    if app.get("is_free") is not False:
        return result("review", "paid_status_unknown")
    row["purchase_options"] = [
        [s.get("packageid"), s.get("option_text"), s.get("price_in_cents_with_discount")]
        for g in app.get("package_groups") or [] for s in g.get("subs") or []
        if not s.get("is_free_license")
    ]
    if not row["purchase_options"]:
        return result("pending" if row["coming_soon"] else "exclude",
                      "awaiting_US_purchase" if row["coming_soon"] else "no_US_purchase_option")
    try:
        options = standard_options(app, row["title"])
    except DiscoveryError as e:
        return result("review", str(e))
    row["standard_ids"] = sorted(options)
    if len(options) != 1:
        return result("review", "standard_US_package_ambiguous")
    pid, cents = next(iter(options.items()))
    row["package_id"] = pid
    purchase = fetch_package(pid)
    try:
        data = parse_price(dict(publisher="BANDAI NAMCO", appid=appid,
                                game_name=row["title"], package_id=pid, tracked_edition="Base"),
                           purchase, "audit")
        package = purchase["body"][str(pid)]["data"]
        row["package_apps"] = [x.get("id") for x in package["apps"]]
        row.update(list_price_usd=data["list_price_usd"], sale_price_usd=data["sale_price_usd"],
                   discount_percent=data["discount_percent"], checked_at=data["observed_at_utc"])
        if set(row["package_apps"]) != {appid}:
            return result("review", "package_contains_other_apps")
        if package["price"]["final"] != cents or cents <= 0:
            return result("review", "US_option_and_package_price_differ")
    except (CollectionError, KeyError, TypeError, ValueError, AttributeError) as e:
        return result("review", "US_package_verification_failed", details=str(e))
    return result("include", "verified_standard_US_purchase")


def main():
    found = {}
    sources = {}
    errors = {}
    for alias in SEARCH_NAMES:
        try:
            ids = search_publisher(alias, fetch_json)
            sources[alias] = len(ids)
            for appid in ids - ALREADY_AUDITED:
                found.setdefault(appid, []).append(alias)
        except (DiscoveryError, ValueError) as e:
            errors[alias] = str(e)
    print("BNE_META " + json.dumps(dict(sources=sources, errors=errors, appids=len(found),
                                        checked_at=timestamp()), ensure_ascii=False), flush=True)
    for appid, names in sorted(found.items()):
        try:
            row = audit(appid, names)
        except (KeyError, TypeError, ValueError, AttributeError) as e:
            row = dict(appid=appid, status="review", reason="unexpected_US_data", details=str(e))
        print("BNE_ROW " + json.dumps(row, ensure_ascii=False, separators=(",", ":")), flush=True)


if __name__ == "__main__":
    main()
