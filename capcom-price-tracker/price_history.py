"""Reconstruct daily price coverage from user-supplied SteamDB UTC exports."""

from __future__ import annotations

import csv
import datetime as dt
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

UTC = dt.timezone.utc
JST = dt.timezone(dt.timedelta(hours=9))


def stamp(value: dt.datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds")


def money(value: str) -> Decimal:
    try:
        price = Decimal(value)
        if not price.is_finite() or price < 0 or price != price.quantize(Decimal("0.01")):
            raise ValueError("Prices must be finite, nonnegative USD amounts with at most two decimals")
        return price
    except InvalidOperation as exc:
        raise ValueError("Invalid USD price") from exc


def reconstruct(path: Path, item: dict, list_price_usd: str) -> tuple[list[dict], list[dict], dict]:
    """Use explicit list price, UTC timestamps, and half-open [start,end) intervals.

    No value is carried beyond the last exported timestamp. A blank price at a
    distinct timestamp opens an unknown interval; a blank duplicate is a chart
    endpoint, not a free license. Historical Low is never used as a list price.
    """
    baseline = money(list_price_usd)
    if baseline <= 0:
        raise ValueError("A positive, independently verified list price is required")
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if not {"DateTime", "Final price"}.issubset(reader.fieldnames or []):
            raise ValueError("Expected SteamDB DateTime and Final price columns")
        raw = list(reader)
    events = {}
    blank_rows = 0
    for row in raw:
        when = dt.datetime.strptime(row["DateTime"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
        if when not in events:
            events[when] = None
        text = row["Final price"].strip()
        if not text:
            blank_rows += 1
            continue
        price = money(text)
        if price > baseline:
            raise ValueError("Observed price exceeds verified baseline; use a reviewed list-price timeline")
        if events[when] is not None and events[when] != price:
            raise ValueError("Conflicting prices at the same UTC timestamp")
        events[when] = price
    dates = sorted(events)
    if len(dates) < 2:
        raise ValueError("At least two timestamps are required to establish coverage")
    intervals, days, unknown_days = [], {}, set()
    for start, end in zip(dates, dates[1:]):
        price = events[start]
        if price is None:
            day = start.astimezone(JST).date()
            while dt.datetime.combine(day, dt.time(), JST).astimezone(UTC) < end:
                unknown_days.add(day.isoformat())
                day += dt.timedelta(days=1)
            continue
        percent = int((100 * (baseline - price) / baseline).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        if price < baseline and percent == 0:
            raise ValueError("Price below baseline is too small to identify an integer Steam discount")
        expected = (baseline * (100 - percent) / 100).quantize(Decimal("0.01"))
        if abs(expected - price) > Decimal("0.01"):
            raise ValueError("Price does not match a whole-percent discount; review baseline or permanent price change")
        intervals.append({"start_utc": stamp(start), "end_utc_exclusive": stamp(end),
                          "start_jst": start.astimezone(JST).isoformat(),
                          "end_jst_exclusive": end.astimezone(JST).isoformat(),
                          "list_price_usd": f"{baseline:.2f}", "sale_price_usd": f"{price:.2f}",
                          "discount_percent": percent, "is_on_sale": int(percent > 0)})
        cursor = start
        while cursor < end:
            day = cursor.astimezone(JST).date()
            midnight = dt.datetime.combine(day + dt.timedelta(days=1), dt.time(), JST).astimezone(UTC)
            until = min(end, midnight)
            key = day.isoformat()
            if key not in days:
                days[key] = {"date_jst": key, **{k: str(item[k]) for k in
                             ("publisher", "appid", "game_name", "tracked_edition", "package_id")},
                             "sample_count": 0, "max_discount_percent": percent,
                             "is_on_sale_any": int(percent > 0), "lowest_sale_price_usd": f"{price:.2f}",
                             "first_observed_utc": stamp(start), "last_observed_utc": stamp(start),
                             "coverage_start_utc": stamp(cursor), "coverage_end_utc_exclusive": stamp(until)}
            else:
                record = days[key]
                record["max_discount_percent"] = max(record["max_discount_percent"], percent)
                record["is_on_sale_any"] = int(bool(record["is_on_sale_any"] or percent))
                record["lowest_sale_price_usd"] = f"{min(Decimal(record['lowest_sale_price_usd']), price):.2f}"
                record["last_observed_utc"] = stamp(start)
                record["coverage_end_utc_exclusive"] = stamp(until)
            cursor = until
    # A gap cannot be converted to a healthy no-sale day. Positive sale evidence
    # remains usable, but do not interpolate across an explicitly missing price.
    days = {k: x for k, x in days.items() if k not in unknown_days or x["is_on_sale_any"]}
    if not intervals:
        raise ValueError("No positive-duration interval with a known price")
    metadata = {"source_rows": len(raw), "priced_rows": sum(bool(r["Final price"].strip()) for r in raw),
                "blank_price_rows": blank_rows, "source_timezone": "UTC", "report_timezone": "Asia/Tokyo",
                "currency": "USD", "list_price_usd": f"{baseline:.2f}",
                "coverage_start_utc": stamp(dates[0]), "coverage_end_utc_exclusive": stamp(dates[-1]),
                "daily_rows": len(days), "sale_days": sum(x["is_on_sale_any"] for x in days.values()),
                "days_touching_unknown_price": len(unknown_days),
                "list_price_basis": "Explicit reviewed baseline; not inferred from Historical Low",
                "day_rule": "Any positive-duration sale interval in a JST calendar day",
                "sample_count": "0 indicates reconstructed history, not API snapshots",
                "observation_fields": "Original source event timestamps contributing price evidence, not synthetic samples",
                "publisher_rates": "Not published from partial per-game historical coverage"}
    return list(days.values()), intervals, metadata


def merge_daily(live: list[dict], historical: list[dict]) -> list[dict]:
    """Merge evidence per JST day without inflating actual API sample counts."""
    def key(row):
        return row["date_jst"], row["publisher"], str(row["appid"])

    merged = {}
    for row in historical:
        k = key(row)
        if k in merged:
            raise ValueError("Duplicate imported game/day; review overlapping sources")
        merged[k] = dict(row)
    for row in live:
        k = key(row)
        old = merged.get(k)
        current = dict(row)
        if old:
            if str(old["package_id"]) != str(row["package_id"]) or old["tracked_edition"] != row["tracked_edition"]:
                raise ValueError("Historical and live purchase editions differ on an overlapping day")
            current["max_discount_percent"] = max(int(old["max_discount_percent"]), int(row["max_discount_percent"]))
            current["is_on_sale_any"] = max(int(old["is_on_sale_any"]), int(row["is_on_sale_any"]))
            current["lowest_sale_price_usd"] = f"{min(Decimal(old['lowest_sale_price_usd']), Decimal(row['lowest_sale_price_usd'])):.2f}"
        merged[k] = current
    return [merged[k] for k in sorted(merged)]
